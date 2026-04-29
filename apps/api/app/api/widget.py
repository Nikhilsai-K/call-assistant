"""
Public agent connect endpoint for the website embed widget.

Anonymous callers visit a customer's website, click the widget, and the widget
hits POST /v1/widget/connect/:agent_id to get a one-shot LiveKit token. Heavy
rate-limiting protects the agent's COGS budget from abuse.
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import get_session
from app.models import Agent
from app.services.livekit_tokens import mint_access_token

router = APIRouter(prefix="/widget", tags=["widget"])

RATE_LIMIT_PER_MIN = 5  # connects per IP per minute
RATE_LIMIT_PER_AGENT_PER_HOUR = 60


class WidgetConnectResponse(BaseModel):
    room: str
    livekit_url: str
    token: str
    agent_name: str
    expires_in: int


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "0.0.0.0")
    )


@router.post("/connect/{agent_id}", response_model=WidgetConnectResponse)
async def connect(agent_id: UUID, request: Request) -> WidgetConnectResponse:
    s = get_settings()
    redis = aioredis.from_url(s.redis_url, decode_responses=True)
    ip = _client_ip(request)
    now_min = int(time.time() // 60)
    now_hour = int(time.time() // 3600)

    ip_key = f"widget.rl.ip.{ip}.{now_min}"
    agent_key = f"widget.rl.agent.{agent_id}.{now_hour}"
    ip_count = await redis.incr(ip_key)
    if ip_count == 1:
        await redis.expire(ip_key, 90)
    agent_count = await redis.incr(agent_key)
    if agent_count == 1:
        await redis.expire(agent_key, 3700)

    if ip_count > RATE_LIMIT_PER_MIN or agent_count > RATE_LIMIT_PER_AGENT_PER_HOUR:
        await redis.aclose()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited")

    async with get_session() as sess:
        agent = await sess.get(Agent, agent_id)
        if (
            agent is None
            or agent.status != "published"
            or (
                "public_widget" not in (agent.tools_enabled or [])
                and not (agent.business_hours or {}).get("widget_public", False)
            )
        ):
            # Allow widget if agent is published and has the widget_public flag in
            # business_hours JSON OR has the explicit "public_widget" pseudo-tool.
            await redis.aclose()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "widget not enabled for this agent")

    room = f"web-{agent_id}-{uuid4().hex[:8]}"
    token = mint_access_token(
        identity=f"web-{uuid4().hex[:8]}",
        room=room,
        ttl_seconds=600,
        metadata=f'{{"agent_id":"{agent_id}","mode":"widget","ip":"{ip}"}}',
    )

    # Push a "widget call requested" event so the agent worker spawns into the room.
    await redis.xadd(
        "vocalflow.inbound.widget",
        {
            "agent_id": str(agent_id),
            "org_id": str(agent.org_id),
            "room": room,
        },
    )
    await redis.aclose()

    return WidgetConnectResponse(
        room=room,
        livekit_url=s.livekit_url,
        token=token,
        agent_name=agent.name,
        expires_in=600,
    )
