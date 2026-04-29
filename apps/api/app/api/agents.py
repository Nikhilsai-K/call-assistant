from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.auth import Principal, current_principal
from app.db.session import get_session
from app.models import Agent
from app.schemas.agents import (
    AgentCreate,
    AgentRead,
    AgentTestTokenResponse,
    AgentUpdate,
)
from app.services.livekit_tokens import mint_access_token
from app.services.prompt_compiler import compile_prompt

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, p: Principal = Depends(current_principal)) -> AgentRead:
    compiled = await compile_prompt(body.plain_instructions, body.personality)
    async with get_session(p.org_id) as s:
        agent = Agent(
            org_id=UUID(p.org_id),
            name=body.name,
            system_prompt=compiled["system_prompt"],
            voice_id=body.voice_id,
            voice_provider=body.voice_provider,
            llm_model=body.llm_model,
            tools_enabled=body.tools_enabled or compiled["tools_enabled"],
            kb_id=body.kb_id,
            business_hours=body.business_hours,
            emergency_keywords=body.emergency_keywords or compiled["emergency_keywords"],
            personality=body.personality.model_dump(),
            status="draft",
        )
        s.add(agent)
        await s.flush()
        await s.refresh(agent)
        return AgentRead.model_validate(agent)


@router.get("", response_model=list[AgentRead])
async def list_agents(p: Principal = Depends(current_principal)) -> list[AgentRead]:
    async with get_session(p.org_id) as s:
        res = await s.execute(select(Agent).where(Agent.org_id == UUID(p.org_id)))
        return [AgentRead.model_validate(a) for a in res.scalars().all()]


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(agent_id: UUID, p: Principal = Depends(current_principal)) -> AgentRead:
    async with get_session(p.org_id) as s:
        agent = await s.get(Agent, agent_id)
        if agent is None or str(agent.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
        return AgentRead.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: UUID, body: AgentUpdate, p: Principal = Depends(current_principal)
) -> AgentRead:
    async with get_session(p.org_id) as s:
        agent = await s.get(Agent, agent_id)
        if agent is None or str(agent.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

        if body.plain_instructions and body.system_prompt is None:
            compiled = await compile_prompt(body.plain_instructions, body.personality)
            agent.system_prompt = compiled["system_prompt"]
        if body.system_prompt:
            agent.system_prompt = body.system_prompt

        for field in ("name", "voice_id", "kb_id", "business_hours", "status"):
            val = getattr(body, field)
            if val is not None:
                setattr(agent, field, val)
        if body.tools_enabled is not None:
            agent.tools_enabled = body.tools_enabled
        if body.emergency_keywords is not None:
            agent.emergency_keywords = body.emergency_keywords
        if body.personality is not None:
            agent.personality = body.personality.model_dump()
        agent.version += 1
        await s.flush()
        await s.refresh(agent)
        return AgentRead.model_validate(agent)


@router.post("/{agent_id}/test", response_model=AgentTestTokenResponse)
async def create_test_session(
    agent_id: UUID, p: Principal = Depends(current_principal)
) -> AgentTestTokenResponse:
    async with get_session(p.org_id) as s:
        agent = await s.get(Agent, agent_id)
        if agent is None or str(agent.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    from app.core.config import get_settings

    settings = get_settings()
    room = f"test-{agent_id}-{uuid4().hex[:8]}"
    token = mint_access_token(
        identity=f"tester-{p.user_id}",
        room=room,
        metadata=f'{{"agent_id":"{agent_id}","mode":"test","org_id":"{p.org_id}"}}',
        ttl_seconds=900,
    )
    return AgentTestTokenResponse(
        room=room, livekit_url=settings.livekit_url, token=token, expires_in=900
    )
