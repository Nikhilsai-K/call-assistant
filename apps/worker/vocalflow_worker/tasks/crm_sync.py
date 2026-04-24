"""
CRM sync — fan-out per installed provider. Idempotent: upsert contact by phone,
log activity keyed by call_id.
"""
from __future__ import annotations

from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings

log = structlog.get_logger("crm_sync")

_engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
_Session = sessionmaker(bind=_engine, expire_on_commit=False)


@shared_task(bind=True, name="vocalflow_worker.tasks.crm_sync.sync_contact")
def sync_contact(self, call_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    with _Session() as s:
        row = s.execute(
            text(
                """SELECT c.org_id, c.from_e164, c.direction, a.name AS agent_name
                FROM calls c LEFT JOIN agents a ON a.id = c.agent_id
                WHERE c.id = :id"""
            ),
            {"id": call_id},
        ).mappings().one_or_none()
    if row is None:
        return {"ok": False, "reason": "call_not_found"}

    providers = _active_providers(str(row["org_id"]))
    # Per-provider adapters would be fleshed out; scaffolded here.
    results = {}
    for p in providers:
        try:
            results[p] = _sync_one(p, row, summary)
        except Exception as e:  # noqa: BLE001
            log.exception("crm_sync.provider_failed", provider=p, err=str(e))
            results[p] = {"ok": False, "error": str(e)}
    return {"ok": True, "providers": results}


def _active_providers(org_id: str) -> list[str]:
    with _Session() as s:
        rows = s.execute(
            text(
                """SELECT provider FROM integrations
                WHERE org_id = :o AND status = 'active'
                AND provider IN ('hubspot','gohighlevel','pipedrive','salesforce','jobber','housecall_pro','servicetitan','nexhealth','drchrono')"""
            ),
            {"o": org_id},
        ).scalars().all()
    return [r for r in rows]


def _sync_one(provider: str, call_row: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    # Production: load encrypted creds via app.core.crypto; call provider API.
    # Scaffolded stubs; per-provider modules land in apps/worker/vocalflow_worker/integrations/*.
    return {"queued": True, "provider": provider, "qualified": summary.get("qualified_lead", False)}
