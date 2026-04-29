"""
HubSpot CRM adapter — lookup + upsert contact, log call activity.
Tokens come from the OAuth flow; this module never sees client secrets.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .base import load_integration

_API = "https://api.hubapi.com"


async def _token(session: AsyncSession, org_id: UUID) -> str | None:
    loaded = await load_integration(session, org_id=org_id, provider="hubspot")
    if loaded is None:
        return None
    creds, _ = loaded
    return creds.get("access_token")


async def lookup(
    session: AsyncSession,
    *,
    org_id: UUID,
    phone: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    token = await _token(session, org_id)
    if token is None:
        return {"found": False}
    filters: list[dict[str, Any]] = []
    if phone:
        filters.append({"propertyName": "phone", "operator": "EQ", "value": phone})
    if email:
        filters.append({"propertyName": "email", "operator": "EQ", "value": email})
    if not filters:
        return {"found": False}
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            f"{_API}/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [{"filters": filters}],
                "properties": ["firstname", "lastname", "email", "phone"],
                "limit": 1,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results", [])
    if not results:
        return {"found": False}
    r = results[0]
    p = r.get("properties", {})
    return {
        "found": True,
        "id": r["id"],
        "first_name": p.get("firstname"),
        "last_name": p.get("lastname"),
        "email": p.get("email"),
        "phone": p.get("phone"),
    }


async def upsert_contact(
    session: AsyncSession,
    *,
    org_id: UUID,
    phone: str,
    name: str | None,
    email: str | None,
    notes: str | None,
) -> dict[str, Any]:
    token = await _token(session, org_id)
    if token is None:
        return {"ok": False, "reason": "hubspot_not_configured"}
    first, last = "", ""
    if name:
        parts = name.strip().split(" ", 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
    payload = {
        "properties": {
            "firstname": first,
            "lastname": last,
            "phone": phone,
            "email": email or "",
            "vocalflow_notes": notes or "",
        }
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        # Try create; if conflict, search + patch.
        resp = await c.post(
            f"{_API}/crm/v3/objects/contacts",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 409:
            existing = await lookup(session, org_id=org_id, phone=phone, email=email)
            if existing.get("found"):
                cid = existing["id"]
                upd = await c.patch(
                    f"{_API}/crm/v3/objects/contacts/{cid}",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                upd.raise_for_status()
                return {"ok": True, "id": cid, "updated": True}
        resp.raise_for_status()
        return {"ok": True, "id": resp.json()["id"], "created": True}


async def log_call(
    session: AsyncSession,
    *,
    org_id: UUID,
    contact_id: str,
    summary_body: str,
    outcome: str,
    duration_s: int | None,
) -> dict[str, Any]:
    token = await _token(session, org_id)
    if token is None:
        return {"ok": False}
    payload = {
        "properties": {
            "hs_call_body": summary_body[:4000],
            "hs_call_disposition": outcome,
            "hs_call_duration": str((duration_s or 0) * 1000),
            "hs_timestamp": "now",
        },
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 194,  # call → contact
                    }
                ],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            f"{_API}/crm/v3/objects/calls",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return {"ok": True, "id": resp.json()["id"]}
