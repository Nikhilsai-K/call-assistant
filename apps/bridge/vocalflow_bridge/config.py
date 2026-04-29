from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    livekit_url: str = "wss://local.livekit"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecret"

    # 8kHz µ-law from Twilio.
    twilio_sample_rate: int = 8000
    # LiveKit publishes 16kHz PCM mono.
    livekit_sample_rate: int = 16000

    log_level: str = "INFO"


settings = BridgeSettings()
