from fastapi import APIRouter

from . import (
    agents,
    analytics,
    calls,
    campaigns,
    dnc,
    integrations,
    kb,
    phone_numbers,
    tools,
    webhooks,
)

api_router = APIRouter(prefix="/v1")

api_router.include_router(agents.router)
api_router.include_router(phone_numbers.router)
api_router.include_router(calls.router)
api_router.include_router(kb.router)
api_router.include_router(integrations.router)
api_router.include_router(campaigns.router)
api_router.include_router(dnc.router)
api_router.include_router(analytics.router)
api_router.include_router(tools.router)
api_router.include_router(webhooks.router)
