"""Consume vocalflow.stripe.events stream, handle subscription + usage events."""
from __future__ import annotations

import json

import redis
import structlog
from celery import shared_task

from ..config import settings

log = structlog.get_logger("stripe_events")
_redis = redis.from_url(settings.redis_url, decode_responses=True)


@shared_task(name="vocalflow_worker.tasks.stripe_events.process")
def process() -> int:
    count = 0
    while True:
        entries = _redis.xread({"vocalflow.stripe.events": "0"}, count=10, block=100)
        if not entries:
            break
        for _, batch in entries:
            for msg_id, fields in batch:
                event = json.loads(fields["event"])
                _handle(event)
                _redis.xdel("vocalflow.stripe.events", msg_id)
                count += 1
    return count


def _handle(event: dict) -> None:
    t = event.get("type", "")
    log.info("stripe.event", type=t)
    # Real implementation maps type -> org billing state changes.
