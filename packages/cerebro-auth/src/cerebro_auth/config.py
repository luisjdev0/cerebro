"""Settings loaded from the environment (and .env for local dev).

A minimal mirror of cerebro_flows.config: same default DATABASE_URL (same
Postgres instance, its own `cerebro_auth` schema). No Redis here -- cerebro-auth
has no ephemeral/mutable state of its own, unlike cerebro-flows.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
    api_token: str = "change-me-dev-token"

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    migrations_dir: str = "db/migrations"

    # Master user, defined by the deployment (see passwords.master_login). It is not stored in
    # the database: it lives in the environment, so changing these and restarting is all it
    # takes to rotate it. Both must be set, and the password needs MIN_PASSWORD_LENGTH characters.
    admin_user: str = ""
    admin_password: SecretStr = SecretStr("")

    # Password login (Phase 1A)
    session_ttl_hours: int = 12
    max_failed_attempts: int = 5
    lockout_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
