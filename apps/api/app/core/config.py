from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://vocalflow:vocalflow@localhost:5432/vocalflow"
    database_url_sync: str = "postgresql://vocalflow:vocalflow@localhost:5432/vocalflow"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_recordings_bucket: str = "vocalflow-recordings"

    livekit_url: str = "wss://local.livekit"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecret"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_sip_domain: str = ""
    twilio_from_number: str = ""

    # Public URL of the Twilio↔LiveKit bridge (wss://...).
    bridge_public_url: str = "wss://media.vocalflow.app"

    # Postmark + Resend (transactional + fallback email).
    postmark_api_token: str = ""
    postmark_from_email: str = "no-reply@vocalflow.app"
    resend_api_key: str = ""

    # Google Calendar OAuth (per-org tokens live in integrations table).
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # HubSpot OAuth.
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""

    # Public dashboard URL (used for embed widget links + payment confirmations).
    public_dashboard_url: str = "http://localhost:3000"

    anthropic_api_key: str = ""
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    cartesia_api_key: str = ""
    cohere_api_key: str = ""

    llm_model_incall: str = "claude-haiku-4-5"
    llm_model_postcall: str = "claude-sonnet-4-5"
    llm_model_judge: str = "claude-opus-4-7"

    qdrant_url: str = "http://localhost:6333"

    langfuse_host: str = "http://localhost:3100"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    slack_signing_secret: str = ""

    # Public API URL used to build Twilio voice webhook URLs etc.
    public_api_url: str = "https://api.vocalflow.app"

    encryption_key: str = "dev-32-byte-key-replace-in-prod-plz"

    feature_semantic_endpointing: bool = True
    feature_voice_biometrics: bool = False
    feature_emotion_routing: bool = True
    feature_backchannels: bool = True
    feature_filler_injection: bool = True

    latency_p95_budget_ms: int = Field(default=500, ge=100, le=2000)

    sentry_dsn: str = ""
    posthog_key: str = ""


_DEV_KEY_MARKER = "dev-32-byte-key"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Fail-closed if the dev encryption key is shipped to production.
    if s.env == "production" and _DEV_KEY_MARKER in s.encryption_key:
        raise RuntimeError(
            "Refusing to start: ENCRYPTION_KEY is the dev default. Set a real "
            "32+ byte secret in the production environment."
        )
    if s.env == "production" and not s.twilio_auth_token and s.twilio_account_sid:
        raise RuntimeError(
            "Refusing to start: TWILIO_AUTH_TOKEN must be set when "
            "TWILIO_ACCOUNT_SID is set in production (signature verification "
            "would silently no-op)."
        )
    return s
