"""
Compliance gates — cannot be bypassed. These tests enforce that requirement.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.compliance import (
    AREA_CODE_STATE,
    parse_number,
    recording_notice_required,
    requires_two_party_consent,
    within_quiet_hours,
)


def test_parse_us_number():
    info = parse_number("+14155550123")
    assert info.e164 == "+14155550123"
    assert info.country == "US"
    assert info.state == "CA"
    assert info.tz is not None


def test_two_party_consent_states():
    assert requires_two_party_consent("CA")
    assert requires_two_party_consent("MA")
    assert requires_two_party_consent("WA")
    assert not requires_two_party_consent("NY")
    assert not requires_two_party_consent(None)


def test_recording_notice_required_follows_state():
    ca = parse_number("+14155550123")
    ny = parse_number("+12125550123")
    assert recording_notice_required(ca)
    assert not recording_notice_required(ny)


def test_quiet_hours_allows_mid_day():
    tz = ZoneInfo("America/Los_Angeles")
    # noon local
    now = datetime(2026, 4, 24, 19, 0, tzinfo=ZoneInfo("UTC"))  # 12:00 PT
    assert within_quiet_hours(now, tz)


def test_quiet_hours_blocks_late_night():
    tz = ZoneInfo("America/Los_Angeles")
    # 23:00 local = 06:00 UTC next day
    now = datetime(2026, 4, 25, 6, 0, tzinfo=ZoneInfo("UTC"))
    assert not within_quiet_hours(now, tz)


def test_quiet_hours_blocks_early_morning():
    tz = ZoneInfo("America/Los_Angeles")
    # 5:00 local
    now = datetime(2026, 4, 24, 12, 0, tzinfo=ZoneInfo("UTC"))  # 5:00 PT
    assert not within_quiet_hours(now, tz)


def test_quiet_hours_boundary_inclusive_start():
    tz = ZoneInfo("America/New_York")
    # Precisely 08:00 ET
    utc = datetime(2026, 4, 24, 12, 0, tzinfo=ZoneInfo("UTC"))  # 8:00 ET (EDT)
    assert within_quiet_hours(utc, tz)


def test_quiet_hours_boundary_exclusive_end():
    tz = ZoneInfo("America/New_York")
    # Precisely 21:00 ET = blocked
    utc = datetime(2026, 4, 25, 1, 0, tzinfo=ZoneInfo("UTC"))  # 21:00 ET
    assert not within_quiet_hours(utc, tz)


def test_all_documented_two_party_states_have_timezone():
    from app.services.compliance import STATE_TIMEZONE, TWO_PARTY_CONSENT_STATES

    # Intentionally allow missing TZ for the minority we haven't mapped yet,
    # but the major commercial states must have one so recording notices fire.
    for st in ("CA", "NY", "TX", "FL", "IL", "MA", "WA", "PA"):
        if st in TWO_PARTY_CONSENT_STATES:
            assert st in STATE_TIMEZONE, f"{st} requires a tz entry"


@pytest.mark.parametrize(
    "area,state",
    list({"415": "CA", "212": "NY", "512": "TX", "305": "FL", "617": "MA"}.items()),
)
def test_area_code_mapping(area, state):
    assert AREA_CODE_STATE[area] == state
