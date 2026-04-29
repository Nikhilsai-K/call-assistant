import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal
from app.core.crypto import encrypt
from app.db.session import get_session
from app.models import Integration

router = APIRouter(prefix="/integrations", tags=["integrations"])

SUPPORTED = {
    "google_calendar",
    "microsoft365",
    "calendly",
    "acuity",
    "cal_com",
    "hubspot",
    "gohighlevel",
    "pipedrive",
    "salesforce",
    "jobber",
    "housecall_pro",
    "servicetitan",
    "nexhealth",
    "drchrono",
    "slack",
    "teams",
    "discord",
    "stripe",
    "twilio",
    "postmark",
    "resend",
}


async def _audit(
    s: AsyncSession,
    org_id: str,
    actor: str,
    action: str,
    target: str,
    metadata: dict | None = None,
) -> None:
    await s.execute(
        text(
            "INSERT INTO audit_log (org_id, actor, action, target, metadata) "
            "VALUES (:o, :a, :ac, :t, :m::jsonb)"
        ),
        {
            "o": org_id,
            "a": actor,
            "ac": action,
            "t": target,
            "m": json.dumps(metadata or {}),
        },
    )


class OAuthStart(BaseModel):
    return_url: str


@router.post("/{provider}/oauth/start")
async def start_oauth(
    provider: str, body: OAuthStart, p: Principal = Depends(current_principal)
) -> dict:
    if provider not in SUPPORTED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported: {provider}")
    return {
        "authorize_url": f"https://oauth.vocalflow.app/{provider}/authorize"
        f"?org={p.org_id}&return={body.return_url}",
    }


@router.post("/{provider}/oauth/callback")
async def oauth_callback(
    provider: str, request: Request, p: Principal = Depends(current_principal)
) -> dict:
    body = await request.json()
    token = body.get("access_token")
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing access_token")

    # Store the FULL OAuth response as JSON so adapters can pull refresh_token,
    # expires_in, scope, etc. — not just the access token. Adapters expect
    # JSON; storing a bare string breaks Google Calendar, HubSpot, etc.
    creds_blob = {
        "access_token": token,
        "refresh_token": body.get("refresh_token"),
        "expires_in": body.get("expires_in"),
        "scope": body.get("scope"),
        "token_type": body.get("token_type", "Bearer"),
    }
    creds_blob = {k: v for k, v in creds_blob.items() if v is not None}
    enc = encrypt(json.dumps(creds_blob))
    config = body.get("config", {})
    async with get_session(p.org_id) as s:
        stmt = pg_insert(Integration).values(
            org_id=UUID(p.org_id),
            provider=provider,
            credentials_encrypted=enc,
            config_json=config,
            status="active",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["org_id", "provider"],
            set_={
                "credentials_encrypted": enc,
                "config_json": config,
                "status": "active",
            },
        )
        await s.execute(stmt)
        await _audit(s, p.org_id, p.user_id, "integration.connected", provider)
    return {"ok": True}


class IntegrationRead(BaseModel):
    provider: str
    status: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[IntegrationRead])
async def list_integrations(
    p: Principal = Depends(current_principal),
) -> list[IntegrationRead]:
    async with get_session(p.org_id) as s:
        res = await s.execute(select(Integration).where(Integration.org_id == UUID(p.org_id)))
        return [IntegrationRead.model_validate(i) for i in res.scalars().all()]


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(provider: str, p: Principal = Depends(current_principal)) -> None:
    async with get_session(p.org_id) as s:
        await s.execute(
            text(
                "UPDATE integrations SET status='disconnected', credentials_encrypted=NULL "
                "WHERE org_id = :o AND provider = :p"
            ),
            {"o": p.org_id, "p": provider},
        )
        await _audit(s, p.org_id, p.user_id, "integration.disconnected", provider)
