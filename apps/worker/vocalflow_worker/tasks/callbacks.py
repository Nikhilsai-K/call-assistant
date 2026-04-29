"""
Callback dispatcher.

agent tool `schedule_callback` puts entries in Redis sorted set
`vocalflow.callbacks.scheduled` with score=unix_when. This task fires every
minute, peels expired entries, and pushes them onto the agent worker's outbound
stream so the dialer places the call (still passes through compliance gates).
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis
import structlog
from celery import shared_task

from ..config import settings

log = structlog.get_logger("callbacks")
_redis = redis.from_url(settings.redis_url, decode_responses=True)


@shared_task(name="vocalflow_worker.tasks.callbacks.fire_due")
def fire_due() -> dict[str, Any]:
    now = time.time()
    expired = _redis.zrangebyscore("vocalflow.callbacks.scheduled", 0, now, start=0, num=50)
    fired = 0
    for raw in expired:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _redis.zrem("vocalflow.callbacks.scheduled", raw)
            continue
        # Need agent_id to dial; skip if not present.
        org_id = payload.get("org_id")
        contact = payload.get("contact")
        if not (org_id and contact):
            _redis.zrem("vocalflow.callbacks.scheduled", raw)
            continue
        # Find a default agent for the org if not specified. Falls back to None.
        agent_id = payload.get("agent_id") or _default_agent_id(org_id)
        if not agent_id:
            log.warn("callback.no_agent", org_id=org_id)
            _redis.zrem("vocalflow.callbacks.scheduled", raw)
            continue
        _redis.xadd(
            "vocalflow.outbound.requests",
            {
                "to": contact,
                "agent_id": agent_id,
                "org_id": org_id,
                "campaign_id": "",
                "metadata": json.dumps({"reason": payload.get("reason"), "callback": True}),
            },
        )
        _redis.zrem("vocalflow.callbacks.scheduled", raw)
        fired += 1
    return {"fired": fired}


def _default_agent_id(org_id: str) -> str | None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(settings.database_url_sync, pool_pre_ping=True)
    with sessionmaker(bind=eng)() as s:
        row = s.execute(
            text(
                "SELECT id FROM agents WHERE org_id = :o AND status = 'published' "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            {"o": org_id},
        ).scalar_one_or_none()
    return str(row) if row else None
