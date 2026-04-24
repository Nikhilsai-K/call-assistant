from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Call(Base):
    """Hypertable partitioned on started_at."""

    __tablename__ = "calls"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    phone_number_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # inbound | outbound
    from_e164: Mapped[str | None] = mapped_column(String, nullable=True)
    to_e164: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sentiment_timeline: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stt_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tts_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    twilio_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handoff_target: Mapped[str | None] = mapped_column(String, nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    livekit_room: Mapped[str | None] = mapped_column(String, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 1), nullable=True)


class CallTranscript(Base):
    __tablename__ = "call_transcripts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    speaker: Mapped[str] = mapped_column(String, nullable=False)  # agent|customer|human_takeover
    text: Mapped[str] = mapped_column(String, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    is_redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CallEvent(Base):
    __tablename__ = "call_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ts_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
