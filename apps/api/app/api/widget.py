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
RATE_LIMIT_PER_ORG_PER_MIN = 30  # protects org COGS even if attacker rotates agent_ids


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


_RATE_LIMIT_LUA = """
local key = KEYS[1]
local cap = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local n = redis.call('INCR', key)
if n == 1 then redis.call('EXPIRE', key, ttl) end
if n > cap then return 1 else return 0 end
"""


@router.post("/connect/{agent_id}", response_model=WidgetConnectResponse)
async def connect(agent_id: UUID, request: Request) -> WidgetConnectResponse:
    s = get_settings()
    redis = aioredis.from_url(s.redis_url, decode_responses=True)
    ip = _client_ip(request)
    now_min = int(time.time() // 60)
    now_hour = int(time.time() // 3600)

    # Resolve agent first so we can rate-limit per-org.
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
            await redis.aclose()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "widget not enabled for this agent")

    org_id = str(agent.org_id)

    # Atomic INCR+EXPIRE per limit, fail-fast on cap breach.
    for key, cap, ttl in (
        (f"widget.rl.ip.{ip}.{now_min}", RATE_LIMIT_PER_MIN, 90),
        (f"widget.rl.agent.{agent_id}.{now_hour}", RATE_LIMIT_PER_AGENT_PER_HOUR, 3700),
        (f"widget.rl.org.{org_id}.{now_min}", RATE_LIMIT_PER_ORG_PER_MIN, 90),
    ):
        breached = await redis.eval(_RATE_LIMIT_LUA, 1, key, cap, ttl)
        if breached:
            await redis.aclose()
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited")

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
