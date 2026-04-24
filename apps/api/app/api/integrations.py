from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

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


class OAuthStart(BaseModel):
    return_url: str


@router.post("/{provider}/oauth/start")
async def start_oauth(
    provider: str, body: OAuthStart, p: Principal = Depends(current_principal)
) -> dict:
    if provider not in SUPPORTED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported: {provider}")
    # Real implementation builds a state-signed URL per provider.
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

    enc = encrypt(token)
    async with get_session(p.org_id) as s:
        stmt = pg_insert(Integration).values(
            org_id=UUID(p.org_id),
            provider=provider,
            credentials_encrypted=enc,
            config_json=body.get("config", {}),
            status="active",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["org_id", "provider"],
            set_={
                "credentials_encrypted": enc,
                "config_json": body.get("config", {}),
                "status": "active",
            },
        )
        await s.execute(stmt)
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
