"""Master user defined in the environment (ADMIN_USER / ADMIN_PASSWORD): not stored in the DB."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cerebro_auth import passwords
from cerebro_auth.api import create_app
from cerebro_auth.config import get_settings

PASSWORD = "master-long-password"


def _sql(query: str, *args):
    async def _run():
        conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(_run())


@pytest.fixture
def master():
    name = f"master-{uuid.uuid4().hex[:6]}"
    try:
        _sql("SELECT 1")
    except Exception:
        pytest.skip("Postgres not reachable at DATABASE_URL")
    settings = get_settings().model_copy(update={"admin_user": name, "admin_password": SecretStr(PASSWORD)})
    passwords._master_failures.clear()
    with TestClient(create_app(settings)) as c:
        yield c, name, settings


def _login(c, name, password):
    return c.post("/login/password", json={"name": name, "password": password})


def test_master_logs_in_without_a_user_row_and_is_admin(master):
    c, name, _ = master
    r = _login(c, name, PASSWORD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_level"] == "admin"
    assert sorted(body["allowed_modules"]) == ["docs", "flows", "memory"]
    assert not _sql("SELECT 1 FROM cerebro_auth.users WHERE name = $1", name)
    # The session works as an admin on the management plane...
    h = {"Authorization": f"Bearer {body['token']}"}
    assert c.get("/users", headers=h).status_code == 200
    # ...and can be logged out like any session.
    assert c.post("/logout", headers=h).status_code == 200
    assert c.get("/users", headers=h).status_code == 401


def test_wrong_password_is_401_and_repeated_failures_lock_with_429(master):
    c, name, settings = master
    for _ in range(settings.max_failed_attempts):
        assert _login(c, name, "wrong-password-here").status_code == 401
    r = _login(c, name, PASSWORD)  # even the right one is refused while locked
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_a_database_user_cannot_take_the_master_name(master):
    c, name, _ = master
    root = {"Authorization": f"Bearer {get_settings().api_token}"}
    r = c.post("/users", json={"name": name, "access_level": "user"}, headers=root)
    assert r.status_code == 409


def test_unset_or_short_password_disables_the_master():
    base = get_settings()
    for user, pw in [("", ""), ("someone", ""), ("someone", "short")]:
        s = base.model_copy(update={"admin_user": user, "admin_password": SecretStr(pw)})
        assert not passwords.master_configured(s)
        assert not passwords.is_master_name(s, "someone")
