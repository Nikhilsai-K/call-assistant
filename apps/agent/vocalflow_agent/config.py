from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://vocalflow:vocalflow@localhost:5432/vocalflow"
    redis_url: str = "redis://localhost:6379/0"
    api_base_url: str = "http://localhost:8000"

    livekit_url: str = "wss://local.livekit"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecret"

    anthropic_api_key: str = ""
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    cartesia_api_key: str = ""

    llm_model_incall: str = "claude-haiku-4-5"

    qdrant_url: str = "http://localhost:6333"
    cohere_api_key: str = ""

    langfuse_host: str = "http://localhost:3100"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_recordings_bucket: str = "vocalflow-recordings"

    # Latency tuning knobs.
    stt_interim_results: bool = True
    stt_endpointing_ms: int = 100
    vad_min_silence_ms: int = 180
    backchannel_silence_threshold_ms: int = 700
    tool_backchannel_threshold_ms: int = 300
    filler_trigger_ms: int = 200

    # Feature flags (default-off for anything that pushes latency > budget).
    feature_semantic_endpointing: bool = True
    feature_backchannels: bool = True
    feature_filler_injection: bool = True
    feature_emotion_routing: bool = True

    # PCI / HIPAA runtime toggles; surfaced into session flags.
    pci_mode_default: bool = False
    hipaa_mode_default: bool = False


settings = AgentSettings()
