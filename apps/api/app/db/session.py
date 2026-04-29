from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session(org_id: str | None = None) -> AsyncIterator[AsyncSession]:
    """Yield a session with tenant org_id bound to the PG session for RLS."""
    async with SessionLocal() as session:
        if org_id is not None:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(org_id)},
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
