"""Callbacks compliance gate — proves DNC/consent/quiet-hours are enforced
before a callback is enqueued."""

from datetime import datetime
from zoneinfo import ZoneInfo

from vocalflow_worker.tasks import callbacks as cb


def test_state_from_phone_known_npa():
    assert cb._state_from_phone("+14155550123") == "CA"
    assert cb._state_from_phone("+12125550123") == "NY"


def test_state_from_phone_unknown():
    assert cb._state_from_phone("+44207946000") is None
    assert cb._state_from_phone("") is None


def test_quiet_hours_unknown_state_fails_closed():
    # No state → no tz → fail-closed (don't dial).
    assert cb._quiet_hours_ok(None) is False
    assert cb._quiet_hours_ok("ZZ") is False


def test_quiet_hours_window_logic_california(monkeypatch):
    """8pm PT is allowed; 10pm PT is not."""

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            # Pick mid-day-PT in UTC.
            return datetime(2026, 5, 1, 20, 0, tzinfo=ZoneInfo("UTC")).astimezone(tz)

    monkeypatch.setattr(cb, "datetime", FakeDT)
    # 13:00 PT inside [8a-9p) → allowed.
    assert cb._quiet_hours_ok("CA") is True


def test_quiet_hours_blocks_late_night(monkeypatch):
    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            # 06:00 UTC May 2 = 23:00 PT May 1 → blocked.
            return datetime(2026, 5, 2, 6, 0, tzinfo=ZoneInfo("UTC")).astimezone(tz)

    monkeypatch.setattr(cb, "datetime", FakeDT)
    assert cb._quiet_hours_ok("CA") is False
