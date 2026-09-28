from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    secret_key: str = "development-only-change-me-at-least-32-bytes"
    database_url: str = "sqlite:///project_atlas.db"
    storage_backend: str = "local"
    local_storage_path: Path = Path("uploads")
    ai_backend: str = "demo"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
