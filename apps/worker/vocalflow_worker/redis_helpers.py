"""
Shared Redis helpers used across worker tasks.

`drain_stream` advances a per-stream cursor stored in Redis itself so we never
re-process old messages on every redrive cycle (the previous "0" pattern reset
the stream every tick).
"""

from __future__ import annotations

from collections.abc import Iterator

import redis

from .config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def drain_stream(stream: str, *, batch: int = 20) -> Iterator[tuple[str, dict[str, str]]]:
    """Yield (message_id, fields) entries from `stream` newer than the stored
    cursor. The cursor is updated and the message is xdel'd only after the
    consumer yields successfully — callers wrap their work in try/except to
    avoid losing entries on crash.

    For at-least-once safety the entries remain in the stream until xdel; if
    the worker dies mid-iteration, the next tick re-reads them.
    """
    r = get_redis()
    cursor_key = f"{stream}.cursor"
    last = r.get(cursor_key) or "0"

    while True:
        entries = r.xread({stream: last}, count=batch, block=100)
        if not entries:
            return
        for _stream_name, batch_entries in entries:
            for msg_id, fields in batch_entries:
                yield msg_id, fields
                # Advance cursor and reclaim space.
                r.set(cursor_key, msg_id)
                r.xdel(stream, msg_id)
                last = msg_id
