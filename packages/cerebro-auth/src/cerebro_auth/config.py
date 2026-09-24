"""Settings loaded from the environment (and .env for local dev).

A minimal mirror of cerebro_flows.config: same default DATABASE_URL (same
Postgres instance, its own `cerebro_auth` schema). No Redis here -- cerebro-auth
has no ephemeral/mutable state of its own, unlike cerebro-flows.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
    api_token: str = "change-me-dev-token"

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    migrations_dir: str = "db/migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
