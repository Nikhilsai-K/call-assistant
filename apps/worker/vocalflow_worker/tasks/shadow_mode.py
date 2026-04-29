"""
Shadow Mode — for the first 14 days of onboarding, the agent silently joins real
human calls (with customer consent), transcribes, and learns business-specific
language, objections, and decisions.

This task runs nightly on a business's shadow-mode calls, extracting:
 - recurring caller questions (becomes auto-FAQ proposals)
 - human agent phrasings (becomes tone/voice calibration samples)
 - objection handling moves (becomes KB examples with retrieval priority)

Outputs propose diffs to the agent's KB, shown in the dashboard for one-click
approval. Nothing auto-merges.
"""

from __future__ import annotations

from typing import Any

import structlog
from anthropic import Anthropic
from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings

log = structlog.get_logger("shadow")

_engine = None
_Session = None


def _get_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session()


@shared_task(name="vocalflow_worker.tasks.shadow_mode.digest_org")
def digest_org(org_id: str) -> dict[str, Any]:
    with _get_session() as s:
        rows = (
            s.execute(
                text(
                    """
                SELECT ct.text, ct.speaker
                FROM call_transcripts ct
                JOIN calls c ON c.id = ct.call_id
                WHERE c.org_id = :o AND c.shadow_mode = TRUE
                  AND c.started_at > NOW() - INTERVAL '1 day'
                ORDER BY ct.start_ms ASC
                LIMIT 5000
                """
                ),
                {"o": org_id},
            )
            .mappings()
            .all()
        )
    convo = "\n".join(f"{r['speaker']}: {r['text']}" for r in rows)
    if not convo.strip() or not settings.anthropic_api_key:
        return {"proposals": []}

    client = Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.llm_model_postcall,
        max_tokens=1500,
        system=(
            "Analyze phone-call transcripts for a small business and propose KB "
            "additions a new voice agent should learn. Return JSON: "
            "{faqs: [{q, a}], phrasings: [str], objection_responses: [{objection, response}]}"
        ),
        messages=[{"role": "user", "content": convo}],
    )
    return {"raw": msg.content[0].text if msg.content else "", "count": len(rows)}
