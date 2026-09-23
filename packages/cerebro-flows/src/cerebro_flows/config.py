"""Settings loaded from the environment (and .env for local dev).

A minimal mirror of cerebro_docs.config: the same default DATABASE_URL (same
Postgres instance, its own `cerebro_flows` schema), plus `redis_url` and
`flow_run_ttl_hours` (luisjdev-pendientes/cerebro-flows SS5 - sliding TTL of the
execution pointer in Redis).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
    api_token: str = "change-me-dev-token"

    redis_url: str = "redis://localhost:6379/0"
    flow_run_ttl_hours: int = 72

    app_host: str = "0.0.0.0"
    app_port: int = 8020

    migrations_dir: str = "db/migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
