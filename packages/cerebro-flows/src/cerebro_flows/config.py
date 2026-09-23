"""Settings loaded from the environment (and .env for local dev).

Espejo minimo de cerebro_docs.config: mismo DATABASE_URL por defecto (misma
instancia Postgres, schema propio `cerebro_flows`), mas `redis_url` y
`flow_run_ttl_hours` (luisjdev-pendientes/cerebro-flows SS5 - TTL deslizante del
puntero de ejecucion en Redis).
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
