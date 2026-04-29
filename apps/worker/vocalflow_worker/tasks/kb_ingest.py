"""
KB ingestion: parse uploaded PDF/DOCX/text and crawl URLs into kb_documents
rows, then chain to kb_index.index_document for embedding.
"""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import boto3
import httpx
import structlog
from bs4 import BeautifulSoup
from celery import shared_task
from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..redis_helpers import drain_stream

log = structlog.get_logger("kb_ingest")

_engine = None
_Session = None


def _get_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session()


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def _extract(content_type: str, raw: bytes) -> tuple[str, str]:
    """Return (title, plain_text)."""
    ct = (content_type or "").lower()
    if "pdf" in ct or raw[:4] == b"%PDF":
        reader = PdfReader(io.BytesIO(raw))
        title = (reader.metadata or {}).get("/Title", "PDF document")
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        return str(title or "PDF document"), text
    if "officedocument.wordprocessingml" in ct or raw[:2] == b"PK":
        try:
            doc = DocxDocument(io.BytesIO(raw))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return "DOCX document", "\n\n".join(paras)
        except Exception:
            pass
    # Fallback: treat as UTF-8 text.
    return "Text document", raw.decode("utf-8", errors="replace")


@shared_task(name="vocalflow_worker.tasks.kb_ingest.parse_upload")
def parse_upload(
    kb_id: str, org_id: str, s3_key: str, filename: str, content_type: str, checksum: str
) -> dict[str, Any]:
    obj = _s3().get_object(Bucket=settings.s3_recordings_bucket, Key=s3_key)
    raw = obj["Body"].read()
    title, body = _extract(content_type, raw)
    if not body.strip():
        return {"ok": False, "reason": "empty_extract"}

    with _get_session() as s:
        # Idempotency on (kb_id, checksum).
        existing = s.execute(
            text("SELECT id FROM kb_documents WHERE kb_id=:k AND checksum=:c"),
            {"k": kb_id, "c": checksum},
        ).scalar_one_or_none()
        if existing:
            doc_id = str(existing)
        else:
            doc_id = str(uuid4())
            s.execute(
                text(
                    "INSERT INTO kb_documents (id, kb_id, title, content, source_url, checksum) "
                    "VALUES (:id, :kb, :t, :c, :u, :ck)"
                ),
                {
                    "id": doc_id,
                    "kb": kb_id,
                    "t": filename or title,
                    "c": body,
                    "u": f"s3://{settings.s3_recordings_bucket}/{s3_key}",
                    "ck": checksum,
                },
            )
            s.commit()
    # Chain into the embedding pipeline.
    from .kb_index import index_document

    index_document.delay(doc_id)
    return {"ok": True, "doc_id": doc_id}


def _robots_allowed(client: httpx.Client, root_url: str):
    """Returns a urllib.robotparser.RobotFileParser. Imported lazily so the
    worker module loads cleanly even if urllib is restricted."""
    from urllib.robotparser import RobotFileParser

    parsed = urlparse(root_url)
    rp = RobotFileParser()
    rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        resp = client.get(rp.url, headers={"User-Agent": "VocalFlowBot/0.1"})
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
            return rp
    except Exception:
        pass
    # No/unreachable robots.txt: allow by default per spec.
    rp.parse(["User-agent: *", "Allow: /"])
    return rp


@shared_task(name="vocalflow_worker.tasks.kb_ingest.crawl_url")
def crawl_url(kb_id: str, org_id: str, root_url: str, max_pages: int = 30) -> dict[str, Any]:
    """Same-host BFS crawl. Strips boilerplate, dedups by content checksum.
    Honors robots.txt, backs off on 429/5xx with exponential delay."""
    import time as _time

    seen: set[str] = set()
    queue: list[str] = [root_url]
    host = urlparse(root_url).netloc
    fetched = 0
    backoff = 1.0

    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        rp = _robots_allowed(client, root_url)
        while queue and fetched < max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not rp.can_fetch("VocalFlowBot/0.1", url):
                continue
            try:
                resp = client.get(url, headers={"User-Agent": "VocalFlowBot/0.1"})
            except Exception:
                continue
            if resp.status_code in (429, 503):
                _time.sleep(min(backoff, 30.0))
                backoff *= 2
                queue.append(url)  # retry once.
                continue
            backoff = 1.0
            if resp.status_code != 200 or "text/html" not in (resp.headers.get("content-type", "")):
                continue
            html = resp.text
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "footer", "nav", "header"]):
                tag.decompose()
            title_raw = soup.title.string if soup.title and soup.title.string else url
            title = str(title_raw).strip()
            text_body = " ".join(soup.get_text(" ").split())
            if len(text_body) < 200:
                # Likely a redirect or shell page; skip indexing but keep crawling.
                pass
            else:
                import hashlib

                checksum = hashlib.sha256(text_body.encode("utf-8")).hexdigest()
                with _get_session() as s:
                    exists = s.execute(
                        text("SELECT id FROM kb_documents WHERE kb_id=:k AND checksum=:c"),
                        {"k": kb_id, "c": checksum},
                    ).scalar_one_or_none()
                    if not exists:
                        doc_id = str(uuid4())
                        s.execute(
                            text(
                                "INSERT INTO kb_documents (id, kb_id, title, content, source_url, checksum) "
                                "VALUES (:id, :kb, :t, :c, :u, :ck)"
                            ),
                            {
                                "id": doc_id,
                                "kb": kb_id,
                                "t": title[:500],
                                "c": text_body,
                                "u": url,
                                "ck": checksum,
                            },
                        )
                        s.commit()
                        from .kb_index import index_document

                        index_document.delay(doc_id)
            fetched += 1

            for a in soup.find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#", 1)[0]
                if urlparse(href).netloc == host and href not in seen:
                    queue.append(href)

    # Update last_synced_at.
    with _get_session() as s:
        s.execute(
            text("UPDATE knowledge_bases SET last_synced_at = NOW() WHERE id = :k"),
            {"k": kb_id},
        )
        s.commit()
    return {"pages_indexed": fetched, "url": root_url}


@shared_task(name="vocalflow_worker.tasks.kb_ingest.redrive")
def redrive() -> int:
    count = 0
    for _msg_id, fields in drain_stream("vocalflow.kb.parse", batch=10):
        if "kb_id" in fields and "s3_key" in fields:
            parse_upload.delay(
                fields["kb_id"],
                fields["org_id"],
                fields["s3_key"],
                fields.get("filename", "upload"),
                fields.get("content_type", ""),
                fields["checksum"],
            )
            count += 1

    for _msg_id, fields in drain_stream("vocalflow.kb.sync", batch=10):
        kb_id = fields.get("kb_id")
        if not kb_id:
            continue
        with _get_session() as s:
            row = (
                s.execute(
                    text("SELECT source_type, source_config FROM knowledge_bases WHERE id=:k"),
                    {"k": kb_id},
                )
                .mappings()
                .one_or_none()
            )
        if row and row["source_type"] == "url":
            url = (
                json.loads(row["source_config"])
                if isinstance(row["source_config"], str)
                else row["source_config"]
            ).get("url")
            if url:
                crawl_url.delay(kb_id, fields["org_id"], url)
                count += 1
    return count
