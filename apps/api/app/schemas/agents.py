from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ToolName = Literal[
    "check_calendar_availability",
    "book_appointment",
    "lookup_customer",
    "create_ticket",
    "transfer_to_human",
    "send_sms",
    "send_email",
    "collect_payment",
    "escalate",
    "schedule_callback",
    "answer_faq",
]


class Personality(BaseModel):
    formality: float = Field(0.5, ge=0, le=1)
    pace: float = Field(0.5, ge=0, le=1)
    warmth: float = Field(0.7, ge=0, le=1)
    verbosity: float = Field(0.4, ge=0, le=1)


class AgentCreate(BaseModel):
    name: str
    plain_instructions: str = Field(
        ...,
        description="Plain-English description; compiled to a structured system prompt",
    )
    voice_id: str = "EXAVITQu4vr4xnSDxMaL"  # ElevenLabs default
    voice_provider: Literal["elevenlabs", "cartesia"] = "elevenlabs"
    llm_model: str = "claude-haiku-4-5"
    tools_enabled: list[ToolName] = Field(default_factory=list)
    kb_id: UUID | None = None
    business_hours: dict[str, Any] | None = None
    emergency_keywords: list[str] = Field(default_factory=list)
    personality: Personality = Field(default_factory=Personality)


class AgentUpdate(BaseModel):
    name: str | None = None
    plain_instructions: str | None = None
    system_prompt: str | None = None
    voice_id: str | None = None
    tools_enabled: list[ToolName] | None = None
    kb_id: UUID | None = None
    business_hours: dict[str, Any] | None = None
    emergency_keywords: list[str] | None = None
    personality: Personality | None = None
    status: Literal["draft", "published", "archived"] | None = None


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    system_prompt: str
    voice_id: str
    voice_provider: str
    llm_model: str
    tools_enabled: list[str]
    kb_id: UUID | None
    emergency_keywords: list[str]
    personality: dict[str, Any]
    version: int
    status: str


class AgentTestTokenResponse(BaseModel):
    room: str
    livekit_url: str
    token: str
    expires_in: int
