import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.core.auth import Principal, current_principal
from app.core.config import get_settings
from app.db.session import get_session
from app.models import Agent, Call, CallTranscript
from app.schemas.calls import CallRead, OutboundCallRequest, TakeoverRequest, WhisperRequest
from app.services.compliance import gate_outbound_call

router = APIRouter(prefix="/calls", tags=["calls"])


# ---- List ----
@router.get("", response_model=list[CallRead])
async def list_calls(limit: int = 50, p: Principal = Depends(current_principal)) -> list[CallRead]:
    limit = max(1, min(200, limit))
    async with get_session(p.org_id) as s:
        res = await s.execute(
            select(Call)
            .where(Call.org_id == UUID(p.org_id))
            .order_by(Call.started_at.desc())
            .limit(limit)
        )
        return [CallRead.model_validate(c) for c in res.scalars().all()]


# ---- Outbound ----
@router.post("/outbound", status_code=status.HTTP_202_ACCEPTED)
async def start_outbound(
    body: OutboundCallRequest, p: Principal = Depends(current_principal)
) -> dict[str, str]:
    """Persist a Call row immediately so the dashboard sees the request, then
    enqueue to the agent worker. The compliance gate is non-bypassable: if it
    raises, no row is committed."""
    from sqlalchemy import text as _t

    new_call_id: str | None = None
    async with get_session(p.org_id) as s:
        agent = await s.get(Agent, body.agent_id)
        if agent is None or str(agent.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
        # Non-bypassable gate.
        await gate_outbound_call(s, org_id=p.org_id, to_e164=body.to)

        result = await s.execute(
            _t(
                "INSERT INTO calls (org_id, agent_id, direction, to_e164, status) "
                "VALUES (:o, :a, 'outbound', :to, 'queued') RETURNING id"
            ),
            {"o": p.org_id, "a": str(body.agent_id), "to": body.to},
        )
        new_call_id = str(result.scalar_one())

    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    queue_id = await redis.xadd(
        "vocalflow.outbound.requests",
        {
            "to": body.to,
            "agent_id": str(body.agent_id),
            "org_id": p.org_id,
            "call_id": new_call_id,
            "campaign_id": str(body.campaign_id) if body.campaign_id else "",
            "metadata": json.dumps(body.metadata),
        },
    )
    await redis.aclose()
    return {"call_id": new_call_id, "queued_id": queue_id, "status": "accepted"}


# ---- Call reads ----
@router.get("/{call_id}", response_model=CallRead)
async def get_call(call_id: UUID, p: Principal = Depends(current_principal)) -> CallRead:
    async with get_session(p.org_id) as s:
        res = await s.execute(select(Call).where(Call.id == call_id, Call.org_id == UUID(p.org_id)))
        call = res.scalar_one_or_none()
        if call is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return CallRead.model_validate(call)


@router.get("/{call_id}/transcript")
async def transcript_snapshot(
    call_id: UUID, p: Principal = Depends(current_principal)
) -> list[dict]:
    async with get_session(p.org_id) as s:
        # Tenant check.
        res = await s.execute(select(Call).where(Call.id == call_id, Call.org_id == UUID(p.org_id)))
        if res.scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)

        q = (
            select(CallTranscript)
            .where(CallTranscript.call_id == call_id)
            .order_by(CallTranscript.start_ms.asc())
        )
        rows = (await s.execute(q)).scalars().all()
        return [
            {
                "speaker": t.speaker,
                "text": t.text,
                "start_ms": t.start_ms,
                "end_ms": t.end_ms,
                "is_redacted": t.is_redacted,
            }
            for t in rows
        ]


@router.get("/{call_id}/transcript/stream")
async def transcript_stream(
    call_id: UUID, p: Principal = Depends(current_principal)
) -> EventSourceResponse:
    """SSE — pushes every transcript turn live via Redis pubsub."""
    # Tenant check up front.
    async with get_session(p.org_id) as s:
        if (
            await s.execute(select(Call).where(Call.id == call_id, Call.org_id == UUID(p.org_id)))
        ).scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)

    channel = f"vocalflow.calls.{call_id}.events"

    async def event_iter() -> AsyncIterator[dict]:
        redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if msg is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "turn", "data": msg["data"]}
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await redis.aclose()

    return EventSourceResponse(event_iter())


# ---- Recording URL ----
@router.get("/{call_id}/recording")
async def recording_url(call_id: UUID, p: Principal = Depends(current_principal)) -> dict:
    async with get_session(p.org_id) as s:
        res = await s.execute(select(Call).where(Call.id == call_id, Call.org_id == UUID(p.org_id)))
        call = res.scalar_one_or_none()
        if call is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if not call.recording_s3_key:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no recording yet")

    # Tenant-path enforcement: every recording key MUST live under
    # `recordings/{org_id}/...`. If the stored key doesn't match, refuse — this
    # protects against a tampered call row pointing at another tenant's object.
    expected_prefix = f"recordings/{p.org_id}/"
    if not call.recording_s3_key.startswith(expected_prefix):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "recording outside tenant scope")

    import asyncio

    import boto3

    settings = get_settings()

    def _sign() -> str:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_recordings_bucket, "Key": call.recording_s3_key},
            ExpiresIn=900,
        )

    url = await asyncio.to_thread(_sign)
    return {"url": url, "expires_in": 900}


# ---- Supervisor controls ----
@router.post("/{call_id}/whisper")
async def whisper(
    call_id: UUID, body: WhisperRequest, p: Principal = Depends(current_principal)
) -> dict:
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    await redis.publish(
        f"vocalflow.calls.{call_id}.supervisor",
        json.dumps({"type": "whisper", "text": body.text, "from": p.user_id}),
    )
    await redis.aclose()
    return {"ok": True}


@router.post("/{call_id}/takeover")
async def takeover(
    call_id: UUID, body: TakeoverRequest, p: Principal = Depends(current_principal)
) -> dict:
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    await redis.publish(
        f"vocalflow.calls.{call_id}.supervisor",
        json.dumps(
            {
                "type": "takeover",
                "human_name": body.human_name,
                "include_brief": body.include_brief,
                "from": p.user_id,
            }
        ),
    )
    await redis.aclose()
    return {"ok": True}
