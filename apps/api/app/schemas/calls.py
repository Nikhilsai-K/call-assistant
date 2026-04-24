from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    agent_id: UUID | None
    direction: str
    from_e164: str | None
    to_e164: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_s: int | None
    outcome: str | None
    recording_s3_key: str | None
    cost_cents: int | None
    handoff_target: str | None
    quality_score: float | None


class OutboundCallRequest(BaseModel):
    to: str
    agent_id: UUID
    campaign_id: UUID | None = None
    metadata: dict[str, Any] = {}


class WhisperRequest(BaseModel):
    text: str


class TakeoverRequest(BaseModel):
    human_name: str
    include_brief: bool = True


class TranscriptTurn(BaseModel):
    speaker: str
    text: str
    start_ms: int
    end_ms: int
    is_redacted: bool = False
