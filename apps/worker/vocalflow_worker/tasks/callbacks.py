"""
Callback dispatcher.

Agent tool `schedule_callback` puts entries in Redis sorted set
`vocalflow.callbacks.scheduled` with score=unix_when. This task fires every
minute, atomically peels expired entries, re-runs the FULL compliance gate
(DNC + prior-consent + quiet hours), and pushes them onto the agent worker's
outbound stream so the dialer places the call.

Per AD-006: compliance is enforced HERE too. We never bypass DNC/consent
just because the customer once asked for a callback.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import redis
import structlog
from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings

log = structlog.get_logger("callbacks")

_redis: redis.Redis | None = None
_engine = None
_Session = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _get_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session()


# Atomic claim: check + remove in one round trip via Lua. Returns the claimed
# entries. If a second worker beats us, it gets a different slice.
_CLAIM_LUA = """
local entries = redis.call('ZRANGEBYSCORE', KEYS[1], 0, ARGV[1], 'LIMIT', 0, ARGV[2])
if #entries > 0 then
    for _, e in ipairs(entries) do
        redis.call('ZREM', KEYS[1], e)
    end
end
return entries
"""

# State -> tz mapping (mirror of api compliance.STATE_TIMEZONE; duplicated to
# avoid pulling the api package as a worker dep).
_STATE_TZ = {
    "CA": "America/Los_Angeles",
    "WA": "America/Los_Angeles",
    "NY": "America/New_York",
    "FL": "America/New_York",
    "MA": "America/New_York",
    "PA": "America/New_York",
    "NH": "America/New_York",
    "CT": "America/New_York",
    "DE": "America/New_York",
    "MD": "America/New_York",
    "MI": "America/Detroit",
    "IL": "America/Chicago",
    "TX": "America/Chicago",
    "MT": "America/Denver",
    "NV": "America/Los_Angeles",
}


def _quiet_hours_ok(state: str | None) -> bool:
    """Return True if it's allowed to dial right now in the recipient's tz.
    Fail-closed: if state/tz is unknown, return False (don't dial)."""
    tz_name = _STATE_TZ.get(state or "")
    if not tz_name:
        return False
    local = datetime.now(tz=ZoneInfo(tz_name)).time()
    return dtime(8, 0) <= local < dtime(21, 0)


def _gate(org_id: str, phone: str) -> tuple[bool, str]:
    """Replicates services/compliance.gate_outbound_call but in sync.
    Returns (allowed, reason)."""
    with _get_session() as s:
        on_dnc = s.execute(
            text("SELECT 1 FROM dnc_list WHERE phone = :p"), {"p": phone}
        ).scalar_one_or_none()
        if on_dnc:
            return False, "dnc"
        consent = s.execute(
            text(
                "SELECT 1 FROM consent_records WHERE phone = :p AND org_id = :o "
                "AND consent_type = 'call' AND revoked_at IS NULL"
            ),
            {"p": phone, "o": org_id},
        ).scalar_one_or_none()
        if not consent:
            return False, "no_consent"
    # Use NPA-mapped state for tz; if not derivable, fail-closed.
    state = _state_from_phone(phone)
    if not _quiet_hours_ok(state):
        return False, f"quiet_hours_or_unknown_tz:{state}"
    return True, "ok"


def _state_from_phone(phone: str) -> str | None:
    # Lightweight NANP mapping; same area-code table the API uses but inlined.
    if not phone or not (phone.startswith("+1") and len(phone) >= 5):
        return None
    npa = phone[2:5]
    return _NPA_STATE.get(npa)


# Subset of api.services.compliance.AREA_CODE_STATE — kept in sync manually.
_NPA_STATE = {
    "213": "CA",
    "310": "CA",
    "415": "CA",
    "510": "CA",
    "619": "CA",
    "650": "CA",
    "707": "CA",
    "714": "CA",
    "747": "CA",
    "805": "CA",
    "818": "CA",
    "831": "CA",
    "858": "CA",
    "909": "CA",
    "916": "CA",
    "925": "CA",
    "949": "CA",
    "212": "NY",
    "315": "NY",
    "347": "NY",
    "516": "NY",
    "585": "NY",
    "607": "NY",
    "631": "NY",
    "646": "NY",
    "716": "NY",
    "718": "NY",
    "845": "NY",
    "914": "NY",
    "917": "NY",
    "929": "NY",
    "210": "TX",
    "214": "TX",
    "254": "TX",
    "281": "TX",
    "346": "TX",
    "361": "TX",
    "409": "TX",
    "430": "TX",
    "432": "TX",
    "469": "TX",
    "512": "TX",
    "713": "TX",
    "806": "TX",
    "817": "TX",
    "830": "TX",
    "903": "TX",
    "915": "TX",
    "936": "TX",
    "940": "TX",
    "956": "TX",
    "972": "TX",
    "979": "TX",
    "239": "FL",
    "305": "FL",
    "321": "FL",
    "352": "FL",
    "386": "FL",
    "407": "FL",
    "561": "FL",
    "727": "FL",
    "754": "FL",
    "772": "FL",
    "786": "FL",
    "813": "FL",
    "850": "FL",
    "863": "FL",
    "904": "FL",
    "941": "FL",
    "954": "FL",
    "217": "IL",
    "224": "IL",
    "309": "IL",
    "312": "IL",
    "331": "IL",
    "618": "IL",
    "630": "IL",
    "708": "IL",
    "773": "IL",
    "779": "IL",
    "815": "IL",
    "847": "IL",
    "872": "IL",
    "339": "MA",
    "351": "MA",
    "413": "MA",
    "508": "MA",
    "617": "MA",
    "774": "MA",
    "781": "MA",
    "857": "MA",
    "978": "MA",
    "206": "WA",
    "253": "WA",
    "360": "WA",
    "425": "WA",
    "509": "WA",
    "564": "WA",
    "215": "PA",
    "267": "PA",
    "412": "PA",
    "484": "PA",
    "570": "PA",
    "610": "PA",
    "717": "PA",
    "724": "PA",
    "814": "PA",
    "878": "PA",
}


@shared_task(name="vocalflow_worker.tasks.callbacks.fire_due")
def fire_due() -> dict[str, Any]:
    r = _get_redis()
    now = time.time()
    raw_entries: list[str] = r.eval(_CLAIM_LUA, 1, "vocalflow.callbacks.scheduled", now, 50)
    fired = 0
    blocked: list[dict[str, str]] = []
    for raw in raw_entries:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        org_id = payload.get("org_id")
        contact = payload.get("contact")
        if not (org_id and contact):
            continue

        agent_id = payload.get("agent_id") or _default_agent_id(org_id)
        if not agent_id:
            log.warn("callback.no_agent", org_id=org_id)
            blocked.append({"contact": contact, "reason": "no_agent"})
            continue

        allowed, reason = _gate(org_id, contact)
        if not allowed:
            log.warn("callback.gated", org_id=org_id, contact=contact, reason=reason)
            blocked.append({"contact": contact, "reason": reason})
            # Re-schedule for the next start of allowed window if quiet-hours;
            # drop entirely if DNC or consent issue.
            if reason.startswith("quiet_hours"):
                # Defer to 8 AM ET next day (rough) — operators tighten via beat.
                next_when = now + 8 * 3600
                r.zadd("vocalflow.callbacks.scheduled", {raw: next_when})
            continue

        r.xadd(
            "vocalflow.outbound.requests",
            {
                "to": contact,
                "agent_id": agent_id,
                "org_id": org_id,
                "campaign_id": "",
                "metadata": json.dumps({"reason": payload.get("reason"), "callback": True}),
            },
        )
        fired += 1
    return {"fired": fired, "blocked": blocked}


def _default_agent_id(org_id: str) -> str | None:
    with _get_session() as s:
        row = s.execute(
            text(
                "SELECT id FROM agents WHERE org_id = :o AND status = 'published' "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            {"o": org_id},
        ).scalar_one_or_none()
    return str(row) if row else None
