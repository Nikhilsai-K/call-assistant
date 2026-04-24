"""
Outbound call placement. Compliance is re-checked here defensively (the API
already checked; this is defense-in-depth in case of direct stream writes).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog
from twilio.rest import Client

from ..config import settings

log = structlog.get_logger("agent.outbound")


async def handle_outbound(fields: dict[str, str]) -> None:
    to = fields["to"]
    agent_id = fields["agent_id"]
    org_id = fields["org_id"]

    # Re-run compliance defensively (DB-level + local).
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with Session() as s:
        await s.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org_id}
        )
        dnc = (
            await s.execute(
                text("SELECT 1 FROM dnc_list WHERE phone = :p"), {"p": to}
            )
        ).scalar_one_or_none()
        if dnc:
            log.warn("outbound.dnc_blocked", to=to)
            return

    # Quiet hours re-check.
    # (Light-weight — in prod we also enforce at the Twilio REST layer.)
    now = datetime.now(tz=ZoneInfo("UTC"))
    hour = now.astimezone(ZoneInfo("America/New_York")).hour
    if hour < 8 or hour >= 21:
        log.warn("outbound.quiet_hours_blocked", to=to)
        return

    if not settings.livekit_url or not settings.twilio_account_sid:
        log.info("outbound.simulated", to=to, agent_id=agent_id)
        return

    client = Client(settings.twilio_account_sid, "")
    room_name = f"out-{uuid4().hex[:10]}"
    twiml_url = (
        f"{settings.api_base_url}/v1/webhooks/twilio/outbound-bridge?room={room_name}"
    )
    client.calls.create(to=to, from_=None, url=twiml_url, record=False)
    # The outbound call hits the bridge → joins LiveKit room → our inbound
    # handler picks up identically. The handler reuses handle_inbound's logic
    # once the caller joins the room.
    log.info("outbound.dialed", to=to, room=room_name)
