"""LiveKit access-token minting for browser test calls and dashboard listen-in."""
from __future__ import annotations

from livekit import api

from app.core.config import get_settings


def mint_access_token(
    *,
    identity: str,
    room: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    can_publish_data: bool = True,
    ttl_seconds: int = 3600,
    metadata: str | None = None,
) -> str:
    s = get_settings()
    token = (
        api.AccessToken(s.livekit_api_key, s.livekit_api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=can_publish_data,
            )
        )
        .with_ttl(ttl_seconds)
    )
    if metadata:
        token = token.with_metadata(metadata)
    return token.to_jwt()
