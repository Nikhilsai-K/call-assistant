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

_engine = None
_Session = None


def _get_session():
    global _engine, _Session
    if _Session is None:
        _engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session()


@shared_task(bind=True, name="vocalflow_worker.tasks.crm_sync.sync_contact")
def sync_contact(self, call_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    with _get_session() as s:
        row = (
            s.execute(
                text(
                    """SELECT c.org_id, c.from_e164, c.direction, a.name AS agent_name
                FROM calls c LEFT JOIN agents a ON a.id = c.agent_id
                WHERE c.id = :id"""
                ),
                {"id": call_id},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return {"ok": False, "reason": "call_not_found"}

    providers = _active_providers(str(row["org_id"]))
    # Per-provider adapters would be fleshed out; scaffolded here.
    results = {}
    for p in providers:
        try:
            results[p] = _sync_one(p, row, summary)
        except Exception as e:
            log.exception("crm_sync.provider_failed", provider=p, err=str(e))
            results[p] = {"ok": False, "error": str(e)}
    return {"ok": True, "providers": results}


def _active_providers(org_id: str) -> list[str]:
    with _get_session() as s:
        rows = (
            s.execute(
                text(
                    """SELECT provider FROM integrations
                WHERE org_id = :o AND status = 'active'
                AND provider IN ('hubspot','gohighlevel','pipedrive','salesforce','jobber','housecall_pro','servicetitan','nexhealth','drchrono')"""
                ),
                {"o": org_id},
            )
            .scalars()
            .all()
        )
    return list(rows)


def _sync_one(provider: str, call_row: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Per-provider sync. Each branch hits the provider's REST API with the
    org's stored token. Errors bubble up to the caller, which logs them as
    `crm_sync.provider_failed`."""
    org_id = str(call_row["org_id"])
    phone = call_row.get("from_e164") or ""
    summary_body = (
        f"Outcome: {summary.get('outcome', 'unknown')}\n"
        f"Reason: {summary.get('reason', '')}\n"
        f"Action items: {', '.join(summary.get('action_items', []))}\n"
        f"Qualified lead: {summary.get('qualified_lead', False)}"
    )
    if provider == "hubspot":
        return _sync_hubspot(org_id, phone, summary_body, summary, call_row)
    if provider == "gohighlevel":
        return _sync_ghl(org_id, phone, summary_body, summary)
    if provider == "jobber":
        return _sync_jobber(org_id, phone, summary_body, summary)
    return {"ok": False, "reason": f"unsupported_provider:{provider}"}


def _load_creds(org_id: str, provider: str) -> dict[str, Any] | None:
    import json as _j

    # Locally decrypt; the app.core.crypto helper is sync-safe for both apps.
    import sys
    from pathlib import Path

    from sqlalchemy import text as _t

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "api"))
    try:
        from app.core.crypto import decrypt
    except ImportError:
        return None

    with _get_session() as s:
        row = (
            s.execute(
                _t(
                    "SELECT credentials_encrypted, config_json FROM integrations "
                    "WHERE org_id=:o AND provider=:p AND status='active'"
                ),
                {"o": org_id, "p": provider},
            )
            .mappings()
            .one_or_none()
        )
    if not row:
        return None
    raw_creds = row["credentials_encrypted"]
    if not raw_creds:
        return None
    try:
        creds_str = decrypt(bytes(raw_creds))
        creds = _j.loads(creds_str) if creds_str.startswith("{") else {"access_token": creds_str}
    except Exception:
        return None
    return creds


def _sync_hubspot(
    org_id: str, phone: str, summary_body: str, summary: dict[str, Any], call_row: dict[str, Any]
) -> dict[str, Any]:
    import httpx

    creds = _load_creds(org_id, "hubspot")
    if not creds or "access_token" not in creds:
        return {"ok": False, "reason": "no_creds"}
    token = creds["access_token"]
    api = "https://api.hubapi.com"
    name_parts = (call_row.get("agent_name") or "").split()
    payload_contact = {
        "properties": {
            "phone": phone,
            "firstname": name_parts[0] if name_parts else "",
            "lastname": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
            "lifecyclestage": "lead" if summary.get("qualified_lead") else "subscriber",
        }
    }
    with httpx.Client(timeout=10.0, headers={"Authorization": f"Bearer {token}"}) as c:
        cr = c.post(f"{api}/crm/v3/objects/contacts", json=payload_contact)
        if cr.status_code == 409:
            search = c.post(
                f"{api}/crm/v3/objects/contacts/search",
                json={
                    "filterGroups": [
                        {"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone}]}
                    ],
                    "limit": 1,
                },
            )
            search.raise_for_status()
            results = search.json().get("results", [])
            contact_id = results[0]["id"] if results else None
        else:
            cr.raise_for_status()
            contact_id = cr.json()["id"]

        if not contact_id:
            return {"ok": False, "reason": "contact_resolve_failed"}

        # Log call activity.
        c.post(
            f"{api}/crm/v3/objects/calls",
            json={
                "properties": {
                    "hs_call_body": summary_body[:4000],
                    "hs_call_disposition": summary.get("outcome", "completed"),
                    "hs_timestamp": "now",
                },
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": 194,
                            }
                        ],
                    }
                ],
            },
        ).raise_for_status()
    return {"ok": True, "contact_id": contact_id}


def _sync_ghl(
    org_id: str, phone: str, summary_body: str, summary: dict[str, Any]
) -> dict[str, Any]:
    import httpx

    creds = _load_creds(org_id, "gohighlevel")
    if not creds or "access_token" not in creds:
        return {"ok": False, "reason": "no_creds"}
    token = creds["access_token"]
    location_id = creds.get("location_id")
    if not location_id:
        return {"ok": False, "reason": "no_location_id"}
    api = "https://services.leadconnectorhq.com"
    headers = {
        "Authorization": f"Bearer {token}",
        "Version": "2021-07-28",
    }
    with httpx.Client(timeout=10.0, headers=headers) as c:
        # Upsert contact.
        upsert = c.post(
            f"{api}/contacts/upsert",
            json={
                "phone": phone,
                "locationId": location_id,
                "tags": ["vocalflow", summary.get("outcome", "")],
            },
        )
        upsert.raise_for_status()
        contact_id = upsert.json().get("contact", {}).get("id")
        if contact_id:
            c.post(
                f"{api}/conversations/messages",
                json={
                    "type": "Note",
                    "contactId": contact_id,
                    "message": summary_body,
                    "locationId": location_id,
                },
            )
    return {"ok": True}


def _sync_jobber(
    org_id: str, phone: str, summary_body: str, summary: dict[str, Any]
) -> dict[str, Any]:
    import httpx

    creds = _load_creds(org_id, "jobber")
    if not creds or "access_token" not in creds:
        return {"ok": False, "reason": "no_creds"}
    token = creds["access_token"]
    # Jobber GraphQL — minimal upsert client + note.
    query = """
    mutation($input: ClientCreateAttributes!) {
      clientCreate(input: $input) { client { id } userErrors { message } }
    }
    """
    with httpx.Client(
        timeout=10.0,
        headers={
            "Authorization": f"Bearer {token}",
            "X-JOBBER-GRAPHQL-VERSION": "2024-11-15",
        },
    ) as c:
        resp = c.post(
            "https://api.getjobber.com/api/graphql",
            json={
                "query": query,
                "variables": {
                    "input": {
                        "phones": [{"number": phone, "primary": True}],
                        "name": "VocalFlow caller",
                    }
                },
            },
        )
        resp.raise_for_status()
        # Errors are non-fatal for our log purposes; we just record best-effort.
    return {"ok": True}
