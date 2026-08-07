"""Settings loaded from the environment (and .env for local dev)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
    api_token: str = "change-me-dev-token"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    migrations_dir: str = "db/migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
