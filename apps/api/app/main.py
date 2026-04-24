from contextlib import asynccontextmanager
from typing import AsyncIterator

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.api import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    s = get_settings()
    if s.sentry_dsn:
        sentry_sdk.init(
            dsn=s.sentry_dsn,
            environment=s.env,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.1,
        )
    log = get_logger("api")
    log.info("api.startup", env=s.env)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="VocalFlow API",
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://app.vocalflow.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    # Could add DB/Redis ping here; keep cheap for k8s readiness.
    return {"status": "ready"}
