"""
Helpers shared across integrations.
Decrypts the org's stored credentials on demand.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models import Integration


async def load_integration(
    session: AsyncSession, *, org_id: UUID, provider: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Returns (credentials, config) for an active integration, or None."""
    res = await session.execute(
        select(Integration).where(
            Integration.org_id == org_id,
            Integration.provider == provider,
            Integration.status == "active",
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        return None
    creds_raw = ""
    if row.credentials_encrypted:
        creds_raw = decrypt(row.credentials_encrypted)
    try:
        creds = json.loads(creds_raw) if creds_raw else {}
    except json.JSONDecodeError:
        # Plain access_token stored as string.
        creds = {"access_token": creds_raw}
    return creds, row.config_json or {}
