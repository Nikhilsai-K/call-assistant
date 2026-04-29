"""Consume vocalflow.stripe.events stream, handle subscription + usage events."""

from __future__ import annotations

import json

import redis
import structlog
from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings

log = structlog.get_logger("stripe_events")
_redis = redis.from_url(settings.redis_url, decode_responses=True)

_engine = None
_Session = None


def _get_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session()


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
    obj = event.get("data", {}).get("object", {})
    log.info("stripe.event", type=t)

    if t in ("customer.subscription.created", "customer.subscription.updated"):
        customer = obj.get("customer")
        plan = (obj.get("items", {}).get("data") or [{}])[0].get("plan", {}).get("nickname")
        with _get_session() as s:
            s.execute(
                text(
                    "UPDATE organizations SET plan = COALESCE(:plan, plan) "
                    "WHERE stripe_customer_id = :cid"
                ),
                {"plan": plan, "cid": customer},
            )
            s.commit()

    elif t == "customer.subscription.deleted":
        customer = obj.get("customer")
        with _get_session() as s:
            s.execute(
                text("UPDATE organizations SET plan = 'cancelled' WHERE stripe_customer_id = :cid"),
                {"cid": customer},
            )
            s.commit()

    elif t == "invoice.payment_failed":
        customer = obj.get("customer")
        log.warn("stripe.payment_failed", customer=customer)
        # Real impl: alert org owner, mark account past_due.

    elif t == "checkout.session.completed":
        # One-shot payment link from collect_payment tool. Mark a related call as paid
        # if metadata carries a call_id.
        meta = obj.get("metadata") or {}
        call_id = meta.get("call_id")
        if call_id:
            with _get_session() as s:
                s.execute(
                    text(
                        "INSERT INTO call_events (call_id, type, payload, ts_ms) "
                        "VALUES (:c, 'payment_completed', :p, EXTRACT(EPOCH FROM NOW())*1000)"
                    ),
                    {"c": call_id, "p": json.dumps({"amount": obj.get("amount_total")})},
                )
                s.commit()
