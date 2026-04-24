"""
Clerk JWT verification. Dashboard/API requests carry `Authorization: Bearer <jwt>`.
JWKS is cached per-process; tokens are verified against Clerk's public keys.

Dev escape: ENV=development + ENV_BYPASS_AUTH_ORG header enables a test-only
shortcut. That path is gated so it cannot be enabled in production.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from app.core.config import get_settings

_JWKS_CACHE: dict[str, Any] = {"keys": None, "expires_at": 0.0}


async def _get_jwks() -> list[dict[str, Any]]:
    s = get_settings()
    now = time.time()
    if _JWKS_CACHE["keys"] and now < _JWKS_CACHE["expires_at"]:
        return _JWKS_CACHE["keys"]
    if not s.clerk_jwks_url:
        return []
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(s.clerk_jwks_url)
        resp.raise_for_status()
        data = resp.json()
    _JWKS_CACHE["keys"] = data["keys"]
    _JWKS_CACHE["expires_at"] = now + 3600
    return data["keys"]


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    role: str


async def verify_clerk_jwt(token: str) -> Principal:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid JWT header: {e}") from e
    keys = await _get_jwks()
    key = next((k for k in keys if k["kid"] == header.get("kid")), None)
    if key is None and s.env != "development":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown signing key")
    try:
        if key:
            claims = jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})
        else:  # dev fallback: unsigned decode for local testing
            claims = jwt.get_unverified_claims(token)
    except JWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid JWT: {e}") from e

    user_id = claims.get("sub")
    org_id = claims.get("org_id") or claims.get("o", {}).get("id") if isinstance(
        claims.get("o"), dict
    ) else claims.get("org_id")
    role = claims.get("role") or (claims.get("o") or {}).get("rol", "member")
    if not user_id or not org_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing sub/org_id in JWT")
    return Principal(user_id=user_id, org_id=org_id, role=role)


async def current_principal(
    authorization: str | None = Header(default=None),
    x_dev_org: str | None = Header(default=None, alias="X-Dev-Org"),
) -> Principal:
    s = get_settings()
    if s.env == "development" and x_dev_org:
        return Principal(user_id="dev-user", org_id=x_dev_org, role="admin")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    return await verify_clerk_jwt(token)


def require_role(*roles: str) -> Any:
    async def _dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return p

    return _dep
