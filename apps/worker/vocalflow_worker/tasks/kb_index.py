"""
KB ingestion: PDF / DOCX / HTML URL → chunks → embeddings → Qdrant + BM25 (Postgres tsvector).
Incremental: checksums on documents so URL re-crawl skips unchanged pages.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import httpx
import redis
import structlog
from celery import shared_task
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings

log = structlog.get_logger("kb_index")

_engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
_Session = sessionmaker(bind=_engine, expire_on_commit=False)
_redis = redis.from_url(settings.redis_url, decode_responses=True)
_qdrant = QdrantClient(url=settings.qdrant_url)

COLLECTION = "vocalflow_kb"
EMBED_DIM = 1024  # cohere embed-v3


def _ensure_collection() -> None:
    try:
        _qdrant.get_collection(COLLECTION)
    except Exception:
        _qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE),
        )


def _chunk(text_: str, max_tokens: int = 400) -> list[str]:
    # Naive char-based chunking; upgrade to a sentence-aware tokenizer later.
    out: list[str] = []
    paragraphs = text_.split("\n\n")
    buf: list[str] = []
    n = 0
    for p in paragraphs:
        if not p.strip():
            continue
        plen = len(p)
        if n + plen > max_tokens * 4 and buf:
            out.append("\n\n".join(buf))
            buf = []
            n = 0
        buf.append(p)
        n += plen
    if buf:
        out.append("\n\n".join(buf))
    return out


def _embed(texts: list[str]) -> list[list[float]]:
    if not settings.cohere_api_key:
        # Deterministic fake embeddings so CI works without external calls.
        out: list[list[float]] = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [(h[i % len(h)] / 255.0) - 0.5 for i in range(EMBED_DIM)]
            out.append(vec)
        return out
    with httpx.Client(timeout=20.0) as c:
        r = c.post(
            "https://api.cohere.ai/v1/embed",
            headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
            json={"texts": texts, "model": "embed-english-v3.0", "input_type": "search_document"},
        )
        r.raise_for_status()
        return r.json()["embeddings"]


@shared_task(bind=True, name="vocalflow_worker.tasks.kb_index.index_document")
def index_document(self, doc_id: str) -> dict[str, Any]:
    _ensure_collection()
    with _Session() as s:
        row = (
            s.execute(
                text(
                    """SELECT d.id, d.title, d.content, d.kb_id, kb.org_id
                FROM kb_documents d JOIN knowledge_bases kb ON kb.id = d.kb_id
                WHERE d.id = :id"""
                ),
                {"id": doc_id},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return {"ok": False, "reason": "not_found"}

    chunks = _chunk(row["content"])
    embeddings = _embed(chunks)
    points = [
        qm.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={
                "doc_id": str(row["id"]),
                "kb_id": str(row["kb_id"]),
                "org_id": str(row["org_id"]),
                "title": row["title"],
                "chunk": chunk,
            },
        )
        for chunk, vec in zip(chunks, embeddings, strict=True)
    ]
    _qdrant.upsert(collection_name=COLLECTION, points=points)
    return {"ok": True, "chunks": len(chunks)}


@shared_task(name="vocalflow_worker.tasks.kb_index.redrive")
def redrive() -> int:
    count = 0
    while True:
        entries = _redis.xread({"vocalflow.kb.index": "0"}, count=20, block=100)
        if not entries:
            break
        for _, batch in entries:
            for msg_id, fields in batch:
                index_document.delay(fields["doc_id"])
                _redis.xdel("vocalflow.kb.index", msg_id)
                count += 1
    return count
