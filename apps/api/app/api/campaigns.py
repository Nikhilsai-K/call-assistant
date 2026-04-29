from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import Principal, current_principal
from app.db.session import get_session
from app.models import Campaign

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str
    agent_id: UUID
    script: str | None = None
    quiet_hours_enforced: bool = True


class CampaignRead(BaseModel):
    id: UUID
    name: str
    status: str
    quiet_hours_enforced: bool

    model_config = {"from_attributes": True}


@router.get("", response_model=list[CampaignRead])
async def list_campaigns(p: Principal = Depends(current_principal)) -> list[CampaignRead]:
    async with get_session(p.org_id) as s:
        res = await s.execute(select(Campaign).where(Campaign.org_id == UUID(p.org_id)))
        return [CampaignRead.model_validate(c) for c in res.scalars().all()]


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreate, p: Principal = Depends(current_principal)
) -> CampaignRead:
    async with get_session(p.org_id) as s:
        c = Campaign(
            org_id=UUID(p.org_id),
            name=body.name,
            agent_id=body.agent_id,
            script=body.script,
            quiet_hours_enforced=body.quiet_hours_enforced,
        )
        s.add(c)
        await s.flush()
        await s.refresh(c)
        return CampaignRead.model_validate(c)


@router.post("/{campaign_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_campaign(campaign_id: UUID, p: Principal = Depends(current_principal)) -> dict:
    async with get_session(p.org_id) as s:
        c = await s.get(Campaign, campaign_id)
        if c is None or str(c.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if not c.dnc_checked:
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                "DNC check must be completed before starting a campaign",
            )
        c.status = "running"

    import redis.asyncio as aioredis

    from app.core.config import get_settings

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    await r.xadd("vocalflow.campaigns.run", {"campaign_id": str(campaign_id)})
    await r.aclose()
    return {"ok": True}
