"""
Inbound PSTN call handling.

Invoked when the API webhook publishes a vocalflow.inbound.pstn event.
Flow:
  1. Claim the job.
  2. Load the agent config (system_prompt, voice_id, tools, KB).
  3. Create the `calls` row (started_at, org_id, direction=inbound).
  4. Mint a LiveKit token for this agent process and join the room.
  5. Subscribe to the incoming participant's audio → CallSession.run(...).
  6. On call end, enqueue post-call job.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import structlog
from livekit import api, rtc
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..config import settings
from ..pipeline.session import CallContext, CallFlags, CallSession
from .audio import LiveKitAudioSink, iter_participant_audio

log = structlog.get_logger("agent.inbound")

_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
_Session = async_sessionmaker(bind=_engine, expire_on_commit=False)


async def _load_agent_config(agent_id: str) -> dict[str, Any]:
    # Use raw SQL to avoid pulling the whole api package as a dep.
    from sqlalchemy import text

    async with _Session() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT id, org_id, system_prompt, voice_id, voice_provider,
                           tools_enabled, kb_id, emergency_keywords
                    FROM agents WHERE id = :id
                    """
                ),
                {"id": agent_id},
            )
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError(f"agent {agent_id} not found")
    return dict(row)


async def _record_call_start(
    call_id: str,
    org_id: str,
    agent_id: str,
    phone_number_id: str | None,
    from_e164: str,
    to_e164: str,
    room: str,
) -> None:
    from sqlalchemy import text

    async with _Session() as s:
        await s.execute(
            text(
                """
                INSERT INTO calls (
                    id, org_id, agent_id, phone_number_id, direction,
                    from_e164, to_e164, livekit_room
                ) VALUES (
                    :id, :org_id, :agent_id, :phone_number_id, 'inbound',
                    :from_e164, :to_e164, :room
                )
                """
            ),
            {
                "id": call_id,
                "org_id": org_id,
                "agent_id": agent_id,
                "phone_number_id": phone_number_id,
                "from_e164": from_e164,
                "to_e164": to_e164,
                "room": room,
            },
        )
        await s.commit()


async def _record_call_end(call_id: str) -> None:
    from sqlalchemy import text

    async with _Session() as s:
        await s.execute(
            text(
                """
                UPDATE calls
                SET ended_at = NOW(),
                    duration_s = EXTRACT(EPOCH FROM (NOW() - started_at))::int
                WHERE id = :id
                """
            ),
            {"id": call_id},
        )
        await s.commit()


async def handle_inbound(fields: dict[str, str]) -> None:
    call_id = str(uuid4())
    room_name = fields["room"]
    agent_id = fields.get("agent_id", "")
    org_id = fields["org_id"]

    if not agent_id:
        log.warn("inbound.no_agent_assigned", number=fields.get("to"))
        return

    agent_cfg = await _load_agent_config(agent_id)
    await _record_call_start(
        call_id=call_id,
        org_id=org_id,
        agent_id=agent_id,
        phone_number_id=fields.get("phone_number_id") or None,
        from_e164=fields.get("from", ""),
        to_e164=fields.get("to", ""),
        room=room_name,
    )

    # Join LiveKit room as the "agent" participant.
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"agent-{call_id}")
        .with_grants(
            api.VideoGrants(
                room_join=True, room=room_name, can_publish=True, can_subscribe=True
            )
        )
        .to_jwt()
    )

    room = rtc.Room()
    await room.connect(settings.livekit_url, token)

    ctx = CallContext(
        call_id=call_id,
        org_id=org_id,
        agent_id=agent_id,
        room=room_name,
        system_prompt=agent_cfg["system_prompt"],
        voice_id=agent_cfg["voice_id"],
        voice_provider=agent_cfg["voice_provider"],
        kb_id=str(agent_cfg["kb_id"]) if agent_cfg.get("kb_id") else None,
        emergency_keywords=list(agent_cfg.get("emergency_keywords") or []),
        tools_enabled=list(agent_cfg.get("tools_enabled") or []),
        flags=CallFlags(),
    )
    sink = LiveKitAudioSink(room)
    await sink.start()

    # Wait for the caller participant to join and publish audio.
    audio_queue: asyncio.Queue[rtc.RemoteAudioTrack] = asyncio.Queue()

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):  # type: ignore[no-untyped-def]
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity != f"agent-{call_id}":
            audio_queue.put_nowait(track)

    caller_track: rtc.RemoteAudioTrack = await asyncio.wait_for(
        audio_queue.get(), timeout=30
    )

    session = CallSession(ctx)
    try:
        await session.run(iter_participant_audio(caller_track), sink)
    finally:
        await _record_call_end(call_id)
        await room.disconnect()
        # Enqueue post-call worker job.
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.xadd(
            "vocalflow.postcall.jobs",
            {"call_id": call_id, "org_id": org_id},
        )
        await r.aclose()
