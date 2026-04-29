"""
Twilio SMS adapter. Each org can connect a Twilio integration with credentials
{ "account_sid": ..., "auth_token": ..., "from_number": "+15555550123" } stored
encrypted. Falls back to global settings.* for the platform default sender.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client

from app.core.config import get_settings

from .base import load_integration


async def _resolve(session: AsyncSession, org_id: UUID) -> tuple[Client, str] | None:
    s = get_settings()
    loaded = await load_integration(session, org_id=org_id, provider="twilio")
    if loaded is not None:
        creds, _ = loaded
        sid = creds.get("account_sid") or s.twilio_account_sid
        token = creds.get("auth_token") or s.twilio_auth_token
        from_n = creds.get("from_number") or s.twilio_from_number
    else:
        sid, token, from_n = s.twilio_account_sid, s.twilio_auth_token, s.twilio_from_number
    if not (sid and token and from_n):
        return None
    return Client(sid, token), from_n


async def send(
    session: AsyncSession,
    *,
    org_id: UUID,
    to: str,
    body: str,
) -> dict[str, Any]:
    resolved = await _resolve(session, org_id)
    if resolved is None:
        return {"sent": False, "reason": "twilio_not_configured"}
    client, from_n = resolved
    msg = client.messages.create(to=to, from_=from_n, body=body)
    return {"sent": True, "sid": msg.sid, "status": msg.status}
