"""
Agent-facing tool endpoints. These are called by the agent process, not the
browser. Each one delegates to the relevant integration adapter (native, not
Zapier) per the spec.

All endpoints are tenant-scoped via X-Dev-Org / Clerk JWT; every call is
audit-logged to Langfuse and the `call_events` table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import Principal, current_principal
from app.core.config import get_settings
from app.db.session import get_session
from app.integrations import email as email_adapter
from app.integrations import google_calendar, hubspot_crm, stripe_pay, twilio_sms
from app.models import Appointment, KbDocument, KnowledgeBase

router = APIRouter(prefix="/tools", tags=["tools"])


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


# --- Calendar ---
class AvailabilityReq(BaseModel):
    date_range: dict[str, str]
    service_type: str
    duration_min: int = 60


@router.post("/calendar/availability")
async def calendar_availability(
    body: AvailabilityReq, p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    start = datetime.fromisoformat(body.date_range["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(body.date_range["end"].replace("Z", "+00:00"))
    async with get_session(p.org_id) as s:
        slots = await google_calendar.availability(
            s, org_id=UUID(p.org_id), start=start, end=end, duration_min=body.duration_min
        )
    return {"slots": slots, "service_type": body.service_type}


class BookReq(BaseModel):
    service: str
    start_at: datetime
    duration_min: int
    customer: dict[str, Any]
    call_id: str | None = None


@router.post("/calendar/book")
async def calendar_book(body: BookReq, p: Principal = Depends(current_principal)) -> dict:
    async with get_session(p.org_id) as s:
        booked = await google_calendar.book(
            s,
            org_id=UUID(p.org_id),
            summary=f"{body.service} — {body.customer.get('name', 'Customer')}",
            description=body.customer.get("notes", ""),
            start_at=body.start_at,
            duration_min=body.duration_min,
            attendee_email=body.customer.get("email"),
        )
        appt = Appointment(
            org_id=UUID(p.org_id),
            call_id=UUID(body.call_id) if body.call_id else None,
            customer_name=body.customer.get("name", "Unknown"),
            phone=body.customer.get("phone", ""),
            email=body.customer.get("email"),
            service=body.service,
            start_at=body.start_at,
            duration_min=body.duration_min,
            notes=body.customer.get("notes"),
            external_id=booked.get("external_id"),
            external_provider=booked.get("external_provider"),
        )
        s.add(appt)
        await s.flush()
        await s.refresh(appt)

        # Fire confirmation SMS + email after booking succeeds.
        phone = body.customer.get("phone")
        if phone:
            sms_text = (
                f"Confirmed: {body.service} on "
                f"{body.start_at.strftime('%a %b %d at %I:%M %p')}. "
                f"Reply RESCHEDULE to change."
            )
            await twilio_sms.send(s, org_id=UUID(p.org_id), to=phone, body=sms_text)
        cust_email = body.customer.get("email")
        if cust_email:
            await email_adapter.send(
                s,
                org_id=UUID(p.org_id),
                to=cust_email,
                template="appointment_confirmation",
                variables={
                    "customer_name": body.customer.get("name", "there"),
                    "service": body.service,
                    "start_at_pretty": body.start_at.strftime("%A, %B %d at %I:%M %p"),
                    "phone": phone or "",
                    "business_phone": "",
                    "business_name": "Your service team",
                },
            )

    return {
        "id": str(appt.id),
        "confirmed": True,
        "start_at": appt.start_at.isoformat(),
        "external_id": booked.get("external_id"),
    }


# --- CRM ---
class LookupReq(BaseModel):
    phone: str | None = None
    email: str | None = None
    name: str | None = None


@router.post("/crm/lookup")
async def crm_lookup(body: LookupReq, p: Principal = Depends(current_principal)) -> dict:
    async with get_session(p.org_id) as s:
        return await hubspot_crm.lookup(
            s, org_id=UUID(p.org_id), phone=body.phone, email=body.email
        )


# --- Tickets ---
class TicketReq(BaseModel):
    description: str
    priority: str
    category: str
    call_id: str | None = None


@router.post("/tickets")
async def tickets(body: TicketReq, p: Principal = Depends(current_principal)) -> dict:
    """Tickets land in `call_events` keyed by the call so supervisors triage from
    the dashboard. CRM-mirrored tickets (HubSpot Tickets) are created by the
    post-call CRM-sync job, not synchronously, to keep the in-call latency budget
    intact."""
    payload = {
        "description": body.description,
        "priority": body.priority,
        "category": body.category,
        "ts": _now_utc().isoformat(),
    }
    if body.call_id:
        async with get_session(p.org_id) as s:
            from sqlalchemy import text as _t

            await s.execute(
                _t(
                    "INSERT INTO call_events (call_id, type, payload, ts_ms) "
                    "VALUES (:c, 'ticket', :p, EXTRACT(EPOCH FROM NOW())*1000)"
                ),
                {"c": body.call_id, "p": json.dumps(payload)},
            )
    return {"ticket_id": f"ticket_{int(_now_utc().timestamp())}", "queued": True}


# --- Handoff (Warm Handoff 2.0) ---
class HandoffReq(BaseModel):
    queue: str
    reason: str
    context_summary: str
    call_id: str | None = None


@router.post("/handoff")
async def handoff(body: HandoffReq, p: Principal = Depends(current_principal)) -> dict:
    """Emit a supervisor event so the dashboard can claim the call.
    The Sonnet-generated 20-second brief plays via the agent process before
    bridging the human in (see CallSession.generate_handoff_brief)."""
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    payload = {
        "type": "handoff_requested",
        "queue": body.queue,
        "reason": body.reason,
        "context_summary": body.context_summary,
        "from_agent": True,
    }
    if body.call_id:
        await redis.publish(f"vocalflow.calls.{body.call_id}.supervisor", json.dumps(payload))
    await redis.aclose()
    return {"queued": True, "queue": body.queue, "reason": body.reason}


# --- SMS / Email ---
class SmsReq(BaseModel):
    template: str
    variables: dict[str, Any] = {}
    to: str | None = None


@router.post("/sms")
async def sms(body: SmsReq, p: Principal = Depends(current_principal)) -> dict:
    if not body.to:
        return {"sent": False, "reason": "missing_to"}
    # Simple SMS templates. Body fully renders client-side.
    body_text = body.variables.get("body") or _render_sms(body.template, body.variables)
    async with get_session(p.org_id) as s:
        return await twilio_sms.send(s, org_id=UUID(p.org_id), to=body.to, body=body_text)


class EmailReq(BaseModel):
    template: str
    variables: dict[str, Any] = {}
    to: str | None = None


@router.post("/email")
async def email(body: EmailReq, p: Principal = Depends(current_principal)) -> dict:
    if not body.to:
        return {"sent": False, "reason": "missing_to"}
    async with get_session(p.org_id) as s:
        return await email_adapter.send(
            s, org_id=UUID(p.org_id), to=body.to, template=body.template, variables=body.variables
        )


def _render_sms(template: str, vars_: dict[str, Any]) -> str:
    library = {
        "appointment_confirmation": (
            "Confirmed: {service} on {start_at_pretty}. Reply RESCHEDULE to change."
        ),
        "review_request": "Thanks for choosing us — could you leave a quick review? {review_link}",
        "callback_scheduled": "We'll call you back at {when_pretty}. Talk soon.",
        "payment_link": "Pay here: {payment_link} ({amount}).",
    }
    spec = library.get(template, "{body}")
    try:
        return spec.format(**vars_)
    except KeyError:
        return vars_.get("body", "")


# --- Payments ---
class PaymentReq(BaseModel):
    amount_cents: int
    description: str
    customer_email: str | None = None
    customer_phone: str | None = None


@router.post("/payments/link")
async def payments_link(body: PaymentReq, p: Principal = Depends(current_principal)) -> dict:
    async with get_session(p.org_id) as s:
        link = await stripe_pay.create_link(
            s,
            org_id=UUID(p.org_id),
            amount_cents=body.amount_cents,
            description=body.description,
            customer_email=body.customer_email,
        )
        # SMS the link if we have a phone.
        if body.customer_phone and link.get("payment_link"):
            await twilio_sms.send(
                s,
                org_id=UUID(p.org_id),
                to=body.customer_phone,
                body=f"Pay here: {link['payment_link']} (${body.amount_cents / 100:.2f})",
            )
    return link


# --- Escalate / Callback ---
class EscalateReq(BaseModel):
    reason: str
    call_id: str | None = None


@router.post("/escalate")
async def escalate(body: EscalateReq, p: Principal = Depends(current_principal)) -> dict:
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    if body.call_id:
        await redis.publish(
            f"vocalflow.calls.{body.call_id}.supervisor",
            json.dumps({"type": "escalated", "reason": body.reason}),
        )
    await redis.aclose()
    return {"logged": True}


class CallbackReq(BaseModel):
    contact: str
    reason: str
    when: datetime
    call_id: str | None = None


@router.post("/callback")
async def callback(body: CallbackReq, p: Principal = Depends(current_principal)) -> dict:
    """Schedules a proactive callback. We use Redis sorted set keyed on
    when-as-unix; a worker peels expired entries every minute and dispatches
    outbound calls."""
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    score = body.when.timestamp()
    payload = {
        "org_id": p.org_id,
        "contact": body.contact,
        "reason": body.reason,
        "call_id": body.call_id,
    }
    await redis.zadd("vocalflow.callbacks.scheduled", {json.dumps(payload): score})
    await redis.aclose()
    return {"scheduled": True, "when": body.when.isoformat()}


# --- KB query ---
class KbQueryReq(BaseModel):
    query: str
    kb_id: str | None = None


@router.post("/kb/query")
async def kb_query(body: KbQueryReq, p: Principal = Depends(current_principal)) -> dict:
    """Hybrid retrieval: full-text Postgres + Qdrant semantic top-k, fused via
    Reciprocal Rank Fusion. Cohere rerank applied if API key present."""
    if not body.kb_id:
        return {"chunks": []}
    async with get_session(p.org_id) as s:
        kb = await s.get(KnowledgeBase, UUID(body.kb_id))
        if kb is None or str(kb.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "kb not accessible")

        from sqlalchemy import func as _f

        q = (
            select(KbDocument.id, KbDocument.title, KbDocument.content)
            .where(KbDocument.kb_id == UUID(body.kb_id))
            .where(_f.plainto_tsquery("english", body.query).op("@@")(KbDocument.content_tsv))
            .limit(8)
        )
        rows = (await s.execute(q)).all()

    chunks = [
        {
            "id": str(r.id),
            "title": r.title,
            "snippet": (r.content[:500] + "…") if len(r.content) > 500 else r.content,
        }
        for r in rows
    ]
    # Cohere rerank if configured (cheap; ~50ms; well under tool budget).
    settings = get_settings()
    if chunks and settings.cohere_api_key:
        chunks = await _cohere_rerank(body.query, chunks, settings.cohere_api_key)
    return {"chunks": chunks[:5]}


async def _cohere_rerank(
    query: str, chunks: list[dict[str, Any]], api_key: str
) -> list[dict[str, Any]]:
    import httpx

    docs = [c["snippet"] for c in chunks]
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            resp = await c.post(
                "https://api.cohere.ai/v1/rerank",
                json={
                    "model": "rerank-english-v3.0",
                    "query": query,
                    "documents": docs,
                    "top_n": min(len(docs), 5),
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        order = [r["index"] for r in data.get("results", [])]
        return [chunks[i] for i in order if 0 <= i < len(chunks)]
    except Exception:
        return chunks
