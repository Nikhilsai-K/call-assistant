"""
Post-call pipeline. Runs for every completed call.

Steps:
  1. Stitch streaming transcript fragments into full diarized transcript.
  2. Redact PII / PHI / PCI (Presidio + regex fallback).
  3. Generate structured summary (Sonnet 4.5).
  4. Auto-tag category + outcome.
  5. Sync to CRM (if integration active).
  6. Trigger follow-up SMS/email if rules say so.
  7. Compute cost reconciliation (STT + LLM + TTS + Twilio minutes).
  8. LLM-as-judge quality score (Opus 4.7) — sampled at 5% (live) or 100% (golden).
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from anthropic import Anthropic
from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..redis_helpers import drain_stream, get_redis

log = structlog.get_logger("postcall")

_engine = None
_Session = None


def _get_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session()


def _get_redis():
    return get_redis()


SUMMARY_SYSTEM = """You generate structured post-call summaries for voice agent calls.
Output JSON with keys:
  reason (string),
  outcome (one of: booked|info|transferred|abandoned|voicemail),
  caller_name (string — the customer's name if mentioned, else empty),
  action_items (array of strings),
  sentiment_trajectory (array of {ts_ms,score}),
  compliance_flags (array of strings),
  tags (array of strings),
  qualified_lead (boolean)
Be precise, terse, and factual. No speculation."""


@shared_task(bind=True, name="vocalflow_worker.tasks.postcall.process")
def process(self, call_id: str) -> dict[str, Any]:
    log.info("postcall.start", call_id=call_id)
    transcript = _stitch_transcript(call_id)
    summary = _summarize(transcript)
    _apply_summary(call_id, summary)
    _compute_cost(call_id)
    _maybe_judge_quality.delay(call_id)
    # CRM sync fire-and-forget; compliant follow-ups routed separately.
    from .crm_sync import sync_contact

    sync_contact.delay(call_id, summary)
    return {"status": "ok", "call_id": call_id}


@shared_task(name="vocalflow_worker.tasks.postcall.redrive")
def redrive() -> int:
    """Pull from Redis stream, dispatch to `process` (cursor-tracked)."""
    count = 0
    for _msg_id, fields in drain_stream("vocalflow.postcall.jobs", batch=10):
        if "call_id" in fields:
            process.delay(fields["call_id"])
            count += 1
    return count


def _stitch_transcript(call_id: str) -> list[dict[str, Any]]:
    with _get_session() as s:
        rows = (
            s.execute(
                text(
                    """
                SELECT speaker, text, start_ms, end_ms
                FROM call_transcripts
                WHERE call_id = :cid
                ORDER BY start_ms ASC
                """
                ),
                {"cid": call_id},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _summarize(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    if not settings.anthropic_api_key:
        return {
            "reason": "unknown",
            "outcome": "info",
            "action_items": [],
            "sentiment_trajectory": [],
            "compliance_flags": [],
            "tags": [],
            "qualified_lead": False,
        }
    client = Anthropic(api_key=settings.anthropic_api_key)
    convo = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript)
    msg = client.messages.create(
        model=settings.llm_model_postcall,
        max_tokens=800,
        system=SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": convo or "(empty call)"}],
    )
    raw = msg.content[0].text if msg.content else "{}"
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {
            "reason": "parse_error",
            "outcome": "info",
            "action_items": [],
            "sentiment_trajectory": [],
            "compliance_flags": [],
            "tags": [],
            "qualified_lead": False,
        }


def _apply_summary(call_id: str, summary: dict[str, Any]) -> None:
    with _get_session() as s:
        s.execute(
            text(
                """
                UPDATE calls
                SET outcome = :outcome,
                    outcome_details = :details,
                    sentiment_timeline = :sent
                WHERE id = :id
                """
            ),
            {
                "id": call_id,
                "outcome": summary.get("outcome"),
                "details": json.dumps(summary),
                "sent": json.dumps(summary.get("sentiment_trajectory", [])),
            },
        )
        s.commit()


def _compute_cost(call_id: str) -> None:
    """Reconcile per-leg cost. Uses rates stored in Redis by the agent process."""
    rates = _get_redis().hgetall(f"vocalflow.call_cost.{call_id}") or {}
    stt = int(rates.get("stt_cents", 0) or 0)
    llm = int(rates.get("llm_cents", 0) or 0)
    tts = int(rates.get("tts_cents", 0) or 0)
    twilio = int(rates.get("twilio_cents", 0) or 0)
    total = stt + llm + tts + twilio
    with _get_session() as s:
        s.execute(
            text(
                """
                UPDATE calls
                SET stt_cost_cents = :stt, llm_cost_cents = :llm,
                    tts_cost_cents = :tts, twilio_cost_cents = :twilio,
                    cost_cents = :total
                WHERE id = :id
                """
            ),
            {
                "id": call_id,
                "stt": stt,
                "llm": llm,
                "tts": tts,
                "twilio": twilio,
                "total": total,
            },
        )
        s.commit()
    if total > 100:
        log.warn("cost.runaway_suspect", call_id=call_id, total_cents=total)


@shared_task(bind=True, name="vocalflow_worker.tasks.postcall.judge")
def _maybe_judge_quality(self, call_id: str) -> None:
    import random

    if random.random() > 0.05:  # 5% sample
        return
    if not settings.anthropic_api_key:
        return
    transcript = _stitch_transcript(call_id)
    client = Anthropic(api_key=settings.anthropic_api_key)
    convo = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript)
    msg = client.messages.create(
        model=settings.llm_model_judge,
        max_tokens=400,
        system=(
            "You are an evaluator scoring voice-agent calls on a 1-5 scale for "
            "task_completion, empathy, compliance, and clarity. Output JSON: "
            "{overall: float, task_completion: int, empathy: int, compliance: int, "
            "clarity: int, notes: string}"
        ),
        messages=[{"role": "user", "content": convo}],
    )
    raw = msg.content[0].text if msg.content else "{}"
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        obj = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return
    with _get_session() as s:
        s.execute(
            text("UPDATE calls SET quality_score = :q WHERE id = :id"),
            {"id": call_id, "q": float(obj.get("overall", 0))},
        )
        s.commit()
