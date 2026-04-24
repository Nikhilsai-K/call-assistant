"""
Compliance gates. These cannot be bypassed in code — even test mode uses the
simulated sandbox which still runs through these checks. Violations are an
unrecoverable exception; no silent fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import phonenumbers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentRecord, DncEntry

# States requiring 2-party consent for call recording.
TWO_PARTY_CONSENT_STATES = {
    "CA", "CT", "DE", "FL", "IL", "MD", "MA", "MI", "MT", "NV", "NH", "PA", "WA",
}

# Rough area-code → state mapping for NANP quiet-hours checks. Prod wires to a
# full NPA/NXX database; this covers the 2026 common codes end-to-end testing.
AREA_CODE_STATE: dict[str, str] = {
    # CA
    "213": "CA", "310": "CA", "415": "CA", "510": "CA", "619": "CA", "650": "CA",
    "707": "CA", "714": "CA", "747": "CA", "805": "CA", "818": "CA", "831": "CA",
    "858": "CA", "909": "CA", "916": "CA", "925": "CA", "949": "CA",
    # NY
    "212": "NY", "315": "NY", "347": "NY", "516": "NY", "585": "NY", "607": "NY",
    "631": "NY", "646": "NY", "716": "NY", "718": "NY", "845": "NY", "914": "NY",
    "917": "NY", "929": "NY",
    # TX
    "210": "TX", "214": "TX", "254": "TX", "281": "TX", "325": "TX", "346": "TX",
    "361": "TX", "409": "TX", "430": "TX", "432": "TX", "469": "TX", "512": "TX",
    "713": "TX", "806": "TX", "817": "TX", "830": "TX", "903": "TX", "915": "TX",
    "936": "TX", "940": "TX", "956": "TX", "972": "TX", "979": "TX",
    # FL
    "239": "FL", "305": "FL", "321": "FL", "352": "FL", "386": "FL", "407": "FL",
    "561": "FL", "727": "FL", "754": "FL", "772": "FL", "786": "FL", "813": "FL",
    "850": "FL", "863": "FL", "904": "FL", "941": "FL", "954": "FL",
    # IL
    "217": "IL", "224": "IL", "309": "IL", "312": "IL", "331": "IL", "618": "IL",
    "630": "IL", "708": "IL", "773": "IL", "779": "IL", "815": "IL", "847": "IL",
    "872": "IL",
    # MA
    "339": "MA", "351": "MA", "413": "MA", "508": "MA", "617": "MA", "774": "MA",
    "781": "MA", "857": "MA", "978": "MA",
    # WA
    "206": "WA", "253": "WA", "360": "WA", "425": "WA", "509": "WA", "564": "WA",
    # PA
    "215": "PA", "267": "PA", "412": "PA", "484": "PA", "570": "PA", "610": "PA",
    "717": "PA", "724": "PA", "814": "PA", "878": "PA",
}

STATE_TIMEZONE: dict[str, str] = {
    "CA": "America/Los_Angeles", "WA": "America/Los_Angeles",
    "NY": "America/New_York", "FL": "America/New_York", "MA": "America/New_York",
    "PA": "America/New_York", "NH": "America/New_York", "CT": "America/New_York",
    "DE": "America/New_York", "MD": "America/New_York", "MI": "America/Detroit",
    "IL": "America/Chicago", "TX": "America/Chicago",
    "MT": "America/Denver", "NV": "America/Los_Angeles",
}

# TCPA quiet-hours window (local to the called number).
QUIET_HOURS_START = time(8, 0)   # 08:00 local
QUIET_HOURS_END = time(21, 0)    # 21:00 local


class ComplianceViolation(Exception):
    """Raised when an outbound call/SMS fails a compliance check."""


@dataclass(frozen=True)
class NumberInfo:
    e164: str
    state: str | None
    country: str
    tz: ZoneInfo | None


def parse_number(raw: str) -> NumberInfo:
    n = phonenumbers.parse(raw, None)
    country = phonenumbers.region_code_for_number(n) or ""
    area = ""
    if country == "US" or country == "CA":
        national = str(n.national_number)
        if len(national) == 10:
            area = national[:3]
    state = AREA_CODE_STATE.get(area)
    tz = ZoneInfo(STATE_TIMEZONE[state]) if state in STATE_TIMEZONE else None
    return NumberInfo(
        e164=phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164),
        state=state,
        country=country,
        tz=tz,
    )


def requires_two_party_consent(state: str | None) -> bool:
    return (state or "") in TWO_PARTY_CONSENT_STATES


def within_quiet_hours(now: datetime, tz: ZoneInfo | None) -> bool:
    """Returns True if the call would be made during allowed hours (not quiet)."""
    if tz is None:
        # Conservative default: only allow during US eastern 8a-9p.
        tz = ZoneInfo("America/New_York")
    local = now.astimezone(tz).time()
    return QUIET_HOURS_START <= local < QUIET_HOURS_END


async def assert_dnc_clear(session: AsyncSession, e164: str) -> None:
    res = await session.execute(select(DncEntry).where(DncEntry.phone == e164))
    if res.scalar_one_or_none() is not None:
        raise ComplianceViolation(f"{e164} is on the DNC list; outbound blocked")


async def assert_prior_consent(
    session: AsyncSession,
    *,
    org_id: str,
    phone: str,
    consent_type: str,
) -> None:
    q = select(ConsentRecord).where(
        ConsentRecord.phone == phone,
        ConsentRecord.org_id == org_id,
        ConsentRecord.consent_type == consent_type,
        ConsentRecord.revoked_at.is_(None),
    )
    if (await session.execute(q)).scalar_one_or_none() is None:
        raise ComplianceViolation(
            f"No active {consent_type} consent record for {phone} in org {org_id}"
        )


async def gate_outbound_call(
    session: AsyncSession,
    *,
    org_id: str,
    to_e164: str,
    now: datetime | None = None,
) -> NumberInfo:
    """Run every hard-required TCPA/DNC check. Raises on any failure."""
    info = parse_number(to_e164)
    now = now or datetime.now(tz=ZoneInfo("UTC"))

    await assert_dnc_clear(session, info.e164)
    await assert_prior_consent(
        session, org_id=org_id, phone=info.e164, consent_type="call"
    )
    if not within_quiet_hours(now, info.tz):
        raise ComplianceViolation(
            f"Outside quiet-hours window (8a-9p local, tz={info.tz}) for {info.e164}"
        )
    return info


def recording_notice_required(info: NumberInfo) -> bool:
    return requires_two_party_consent(info.state)
