from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    database_url_sync: str = "postgresql://vocalflow:vocalflow@localhost:5432/vocalflow"

    anthropic_api_key: str = ""
    llm_model_postcall: str = "claude-sonnet-4-5"
    llm_model_judge: str = "claude-opus-4-7"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_recordings_bucket: str = "vocalflow-recordings"

    qdrant_url: str = "http://localhost:6333"
    cohere_api_key: str = ""


settings = WorkerSettings()
