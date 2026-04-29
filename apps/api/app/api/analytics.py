from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.auth import Principal, current_principal
from app.db.session import get_session
from app.models import Call

router = APIRouter(prefix="/analytics", tags=["analytics"])


class CallAnalytics(BaseModel):
    total: int
    booked: int
    abandoned: int
    transferred: int
    avg_duration_s: float | None
    p95_quality: float | None


class CostAnalytics(BaseModel):
    total_cents: int
    stt_cents: int
    llm_cents: int
    tts_cents: int
    twilio_cents: int
    per_call_cents_avg: float | None


@router.get("/calls", response_model=CallAnalytics)
async def call_analytics(
    from_: datetime | None = None,
    to: datetime | None = None,
    p: Principal = Depends(current_principal),
) -> CallAnalytics:
    from_ = from_ or datetime.fromtimestamp(0, tz=UTC)
    to = to or datetime.now(tz=UTC)
    async with get_session(p.org_id) as s:
        q = select(
            func.count().label("total"),
            func.count().filter(Call.outcome == "booked").label("booked"),
            func.count().filter(Call.outcome == "abandoned").label("abandoned"),
            func.count().filter(Call.outcome == "transferred").label("transferred"),
            func.avg(Call.duration_s).label("avg_dur"),
            func.percentile_cont(0.95).within_group(Call.quality_score).label("p95q"),
        ).where(
            Call.org_id == UUID(p.org_id),
            Call.started_at >= from_,
            Call.started_at <= to,
        )
        row = (await s.execute(q)).one()
        return CallAnalytics(
            total=row.total or 0,
            booked=row.booked or 0,
            abandoned=row.abandoned or 0,
            transferred=row.transferred or 0,
            avg_duration_s=float(row.avg_dur) if row.avg_dur is not None else None,
            p95_quality=float(row.p95q) if row.p95q is not None else None,
        )


@router.get("/cost", response_model=CostAnalytics)
async def cost_analytics(
    from_: datetime | None = None,
    to: datetime | None = None,
    p: Principal = Depends(current_principal),
) -> CostAnalytics:
    from_ = from_ or datetime.fromtimestamp(0, tz=UTC)
    to = to or datetime.now(tz=UTC)
    async with get_session(p.org_id) as s:
        q = select(
            func.coalesce(func.sum(Call.cost_cents), 0).label("total"),
            func.coalesce(func.sum(Call.stt_cost_cents), 0).label("stt"),
            func.coalesce(func.sum(Call.llm_cost_cents), 0).label("llm"),
            func.coalesce(func.sum(Call.tts_cost_cents), 0).label("tts"),
            func.coalesce(func.sum(Call.twilio_cost_cents), 0).label("twilio"),
            func.avg(Call.cost_cents).label("avg_call"),
        ).where(
            Call.org_id == UUID(p.org_id),
            Call.started_at >= from_,
            Call.started_at <= to,
        )
        row = (await s.execute(q)).one()
        return CostAnalytics(
            total_cents=row.total,
            stt_cents=row.stt,
            llm_cents=row.llm,
            tts_cents=row.tts,
            twilio_cents=row.twilio,
            per_call_cents_avg=float(row.avg_call) if row.avg_call is not None else None,
        )
