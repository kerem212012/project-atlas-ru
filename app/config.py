from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    secret_key: str = "development-only-change-me-at-least-32-bytes"
    database_url: str = "sqlite:///project_atlas.db"
    storage_backend: str = "local"
    local_storage_path: Path = Path("uploads")
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "project-atlas"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    ai_backend: str = "demo"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = Field(default=20, gt=0, le=120)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
