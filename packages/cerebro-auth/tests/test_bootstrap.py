"""First-run admin bootstrap (ADMIN_USER / ADMIN_PASSWORD)."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from pydantic import SecretStr

from cerebro_auth.bootstrap import bootstrap_admin
from cerebro_auth.config import get_settings
from cerebro_auth.db import apply_migrations, create_pool
from cerebro_auth.passwords import check_user_password


def _admin_dsn(dsn: str) -> str:
    return dsn.rsplit("/", 1)[0] + "/postgres"


def _with_db(dsn: str, name: str) -> str:
    return dsn.rsplit("/", 1)[0] + "/" + name


def _run_in_fresh_database(coro_factory):
    """Run `coro_factory(settings, pool)` against an empty, migrated throwaway database."""
    base = get_settings()
    dbname = f"cerebro_bootstrap_{uuid.uuid4().hex[:8]}"

    async def _go():
        try:
            admin = await asyncpg.connect(dsn=_admin_dsn(base.database_url), timeout=8)
        except Exception:
            pytest.skip("Postgres not reachable at DATABASE_URL")
        await admin.execute(f'CREATE DATABASE "{dbname}"')
        settings = base.model_copy(update={"database_url": _with_db(base.database_url, dbname)})
        pool = await create_pool(settings)
        try:
            await apply_migrations(pool, settings)
            return await coro_factory(settings, pool)
        finally:
            await pool.close()
            await admin.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')
            await admin.close()

    return asyncio.run(_go())


def _cfg(settings, **kw):
    return settings.model_copy(update={"admin_password": SecretStr(kw.pop("password", "")), **kw})


def test_creates_admin_on_empty_database_and_it_can_log_in():
    async def go(settings, pool):
        s = _cfg(settings, admin_user="root-admin", password="a-long-enough-pass")
        assert await bootstrap_admin(pool, s) is True
        row = await pool.fetchrow("SELECT access_level FROM cerebro_auth.users WHERE name='root-admin'")
        assert row["access_level"] == "admin"
        await check_user_password(pool, s, "root-admin", "a-long-enough-pass")
        # Second start: users exist, nothing changes.
        assert await bootstrap_admin(pool, s) is False
        assert await pool.fetchval("SELECT count(*) FROM cerebro_auth.users") == 1

    _run_in_fresh_database(go)


def test_never_overwrites_or_adds_when_users_exist():
    async def go(settings, pool):
        await pool.execute("INSERT INTO cerebro_auth.users (name, access_level) VALUES ('someone','user')")
        s = _cfg(settings, admin_user="root-admin", password="a-long-enough-pass")
        assert await bootstrap_admin(pool, s) is False
        assert await pool.fetchval("SELECT count(*) FROM cerebro_auth.users") == 1

    _run_in_fresh_database(go)


@pytest.mark.parametrize(
    ("user", "password"),
    [("", ""), ("admin", ""), ("", "a-long-enough-pass"), ("admin", "short")],
)
def test_skips_when_unset_incomplete_or_weak(user, password):
    async def go(settings, pool):
        s = _cfg(settings, admin_user=user, password=password)
        assert await bootstrap_admin(pool, s) is False
        assert await pool.fetchval("SELECT count(*) FROM cerebro_auth.users") == 0

    _run_in_fresh_database(go)
