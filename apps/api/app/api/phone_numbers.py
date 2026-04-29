from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from twilio.rest import Client

from app.core.auth import Principal, current_principal
from app.core.config import get_settings
from app.db.session import get_session
from app.models import PhoneNumber

router = APIRouter(prefix="/phone-numbers", tags=["phone-numbers"])


class ProvisionRequest(BaseModel):
    area_code: str | None = None
    country: str = "US"
    agent_id: UUID | None = None


class PhoneNumberRead(BaseModel):
    id: UUID
    e164: str
    provider: str
    agent_id: UUID | None
    inbound_enabled: bool
    outbound_enabled: bool

    model_config = {"from_attributes": True}


@router.post("/provision", response_model=PhoneNumberRead, status_code=status.HTTP_201_CREATED)
async def provision(
    body: ProvisionRequest, p: Principal = Depends(current_principal)
) -> PhoneNumberRead:
    s = get_settings()
    if not s.twilio_account_sid or not s.twilio_auth_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "twilio not configured")

    client = Client(s.twilio_account_sid, s.twilio_auth_token)
    search = client.available_phone_numbers(body.country).local.list(
        area_code=body.area_code, limit=1
    )
    if not search:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no numbers available")
    bought = client.incoming_phone_numbers.create(
        phone_number=search[0].phone_number,
        voice_url=f"{s.public_api_url}/v1/webhooks/twilio/voice",
        sms_url=f"{s.public_api_url}/v1/webhooks/twilio/sms",
    )

    async with get_session(p.org_id) as session:
        pn = PhoneNumber(
            org_id=UUID(p.org_id),
            e164=bought.phone_number,
            provider="twilio",
            provider_sid=bought.sid,
            agent_id=body.agent_id,
            inbound_enabled=True,
        )
        session.add(pn)
        await session.flush()
        await session.refresh(pn)
        return PhoneNumberRead.model_validate(pn)


@router.get("", response_model=list[PhoneNumberRead])
async def list_numbers(p: Principal = Depends(current_principal)) -> list[PhoneNumberRead]:
    async with get_session(p.org_id) as s:
        res = await s.execute(select(PhoneNumber).where(PhoneNumber.org_id == UUID(p.org_id)))
        return [PhoneNumberRead.model_validate(n) for n in res.scalars().all()]
