"""Settings loaded from the environment (and .env for local dev).

Minimal mirror of cerebro_memory.config: same default DATABASE_URL (same
Postgres instance, see ecosistema-cerebro.md SS8), but without any
embeddings/Context Engine - cerebro-docs doesn't do semantic retrieval.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
    api_token: str = "change-me-dev-token"

    app_host: str = "0.0.0.0"
    app_port: int = 8010

    migrations_dir: str = "db/migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
