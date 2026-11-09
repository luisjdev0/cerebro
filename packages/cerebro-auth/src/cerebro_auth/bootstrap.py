"""First-run admin bootstrap.

A fresh deployment has no users, and the root token (`API_TOKEN`) is not a login: nobody could
sign in to the portal without first running the CLI. When `ADMIN_USER` and `ADMIN_PASSWORD` are
set, the service creates that admin on startup -- but only while the `users` table is empty, so
it is a one-time bootstrap: it never overwrites an existing user's password, and changing the
variables later does nothing (rotate with `cerebro user set-password` or the portal).
"""

from __future__ import annotations

import logging

import asyncpg

from cerebro_auth.config import Settings
from cerebro_auth.passwords import MIN_PASSWORD_LENGTH, hash_password

logger = logging.getLogger(__name__)


async def bootstrap_admin(pool: asyncpg.Pool, settings: Settings) -> bool:
    """Create the initial admin from the environment. Returns True if a user was created."""
    name = settings.admin_user.strip()
    password = settings.admin_password.get_secret_value()
    if not name and not password:
        return False
    if not name or not password:
        logger.warning("ADMIN_USER and ADMIN_PASSWORD must be set together; skipping admin bootstrap")
        return False
    if len(password) < MIN_PASSWORD_LENGTH:
        logger.error("ADMIN_PASSWORD is shorter than %d characters; skipping admin bootstrap", MIN_PASSWORD_LENGTH)
        return False

    async with pool.acquire() as conn, conn.transaction():
        # Serialize concurrent starts (several replicas) so only one creates the user.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('cerebro_auth.bootstrap_admin'))")
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM cerebro_auth.users)"):
            return False
        await conn.execute(
            """
            INSERT INTO cerebro_auth.users (name, email, access_level, password_hash)
            VALUES ($1, $2, 'admin', $3)
            """,
            name,
            settings.admin_email or None,
            hash_password(password),
        )
    logger.info("created initial admin user '%s' from ADMIN_USER/ADMIN_PASSWORD", name)
    return True
