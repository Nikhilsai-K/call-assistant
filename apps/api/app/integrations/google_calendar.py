"""
Google Calendar adapter. Uses the org's OAuth refresh-token to mint a fresh
access token; lists free/busy slots and creates events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

from .base import load_integration

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CAL_API = "https://www.googleapis.com/calendar/v3"


async def _refresh_access_token(refresh_token: str) -> str:
    s = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            _TOKEN_URL,
            data={
                "client_id": s.google_oauth_client_id,
                "client_secret": s.google_oauth_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _client(session: AsyncSession, org_id: UUID) -> tuple[str, str] | None:
    loaded = await load_integration(session, org_id=org_id, provider="google_calendar")
    if loaded is None:
        return None
    creds, config = loaded
    refresh = creds.get("refresh_token")
    if not refresh:
        return None
    access = await _refresh_access_token(refresh)
    cal_id = config.get("calendar_id", "primary")
    return access, cal_id


async def availability(
    session: AsyncSession,
    *,
    org_id: UUID,
    start: datetime,
    end: datetime,
    duration_min: int = 60,
) -> list[dict[str, Any]]:
    """Returns up to 5 open slots in the [start, end] window using freebusy."""
    client = await _client(session, org_id)
    if client is None:
        # Synthesized fallback so the agent can demo without a connected calendar.
        seed = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
        return [
            {"start": (seed + timedelta(hours=h)).isoformat(), "duration_min": duration_min}
            for h in (0, 3, 26, 29, 50)
        ]
    access, cal_id = client
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            f"{_CAL_API}/freeBusy",
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": cal_id}],
            },
            headers={"Authorization": f"Bearer {access}"},
        )
        resp.raise_for_status()
        busy = resp.json()["calendars"][cal_id].get("busy", [])

    # Greedy slot finder: walk start→end in 30-min steps, skip busy windows.
    busy_ranges = [
        (
            datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
        )
        for b in busy
    ]
    slot_len = timedelta(minutes=duration_min)
    step = timedelta(minutes=30)
    slots: list[dict[str, Any]] = []
    cur = start
    while cur + slot_len <= end and len(slots) < 5:
        clash = any(bs < cur + slot_len and be > cur for bs, be in busy_ranges)
        if not clash and cur.hour >= 8 and (cur + slot_len).hour <= 18:
            slots.append({"start": cur.isoformat(), "duration_min": duration_min})
        cur += step
    return slots


async def book(
    session: AsyncSession,
    *,
    org_id: UUID,
    summary: str,
    description: str,
    start_at: datetime,
    duration_min: int,
    attendee_email: str | None,
) -> dict[str, Any]:
    client = await _client(session, org_id)
    if client is None:
        return {"external_id": None, "external_provider": None}
    access, cal_id = client
    end_at = start_at + timedelta(minutes=duration_min)
    body: dict[str, Any] = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_at.isoformat()},
        "end": {"dateTime": end_at.isoformat()},
    }
    if attendee_email:
        body["attendees"] = [{"email": attendee_email}]
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            f"{_CAL_API}/calendars/{cal_id}/events",
            json=body,
            headers={"Authorization": f"Bearer {access}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "external_id": data["id"],
        "external_provider": "google_calendar",
        "html_link": data.get("htmlLink"),
    }
