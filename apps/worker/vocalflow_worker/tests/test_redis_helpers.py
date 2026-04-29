"""Cursor-based stream draining: verify it advances and doesn't reread."""

from unittest.mock import MagicMock

from vocalflow_worker import redis_helpers


def test_drain_stream_advances_cursor(monkeypatch):
    fake = MagicMock()
    # First call returns 2 entries, second returns nothing.
    fake.get.return_value = "0"
    fake.xread.side_effect = [
        [("vocalflow.x", [("100-0", {"a": "1"}), ("101-0", {"a": "2"})])],
        [],
    ]
    monkeypatch.setattr(redis_helpers, "get_redis", lambda: fake)

    seen = list(redis_helpers.drain_stream("vocalflow.x", batch=10))
    assert [m for m, _ in seen] == ["100-0", "101-0"]
    # Cursor advanced and entries were xdel'd.
    assert fake.set.call_count == 2
    assert fake.xdel.call_count == 2


def test_drain_stream_empty_returns_immediately(monkeypatch):
    fake = MagicMock()
    fake.get.return_value = "0"
    fake.xread.return_value = []
    monkeypatch.setattr(redis_helpers, "get_redis", lambda: fake)
    assert list(redis_helpers.drain_stream("vocalflow.empty")) == []
