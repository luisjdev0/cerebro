"""Tests for password login (Phase 1A): hashing, session tokens, expiry, lockout,
password changes and logout. Same client/root_headers pattern as test_auth.py."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_auth.api import create_app
from cerebro_auth.config import get_settings
from cerebro_auth.passwords import hash_password, verify_password

GOOD_PASSWORD = "correct-horse-battery"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _db_reachable(dsn: str) -> bool:
    async def _check() -> None:
        conn = await asyncpg.connect(dsn=dsn, timeout=8)
        await conn.close()

    try:
        asyncio.run(_check())
        return True
    except Exception:
        return False


def _sql(query: str, *args) -> None:
    """Run a raw statement against the test database (to backdate expiry, etc.)."""

    async def _run() -> None:
        conn = await asyncpg.connect(dsn=get_settings().database_url)
        try:
            await conn.execute(query, *args)
        finally:
            await conn.close()

    asyncio.run(_run())


# --------------------------------------------------------------------------- unit


def test_hash_is_argon2id_and_never_the_plaintext():
    h = hash_password(GOOD_PASSWORD)
    assert h.startswith("$argon2id$")
    assert GOOD_PASSWORD not in h


def test_verify_password_accepts_right_and_rejects_wrong():
    h = hash_password(GOOD_PASSWORD)
    assert verify_password(h, GOOD_PASSWORD) is True
    assert verify_password(h, "wrong-password-here") is False


def test_verify_password_with_no_hash_is_always_false():
    assert verify_password(None, GOOD_PASSWORD) is False


# --------------------------------------------------------------------------- integration


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not _db_reachable(settings.database_url):
        pytest.skip("Postgres not reachable at DATABASE_URL")
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def root_headers():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


def _create_user(client, root_headers, *, access_level="user", password=GOOD_PASSWORD) -> str:
    name = _unique("pw")
    body = {"name": name, "access_level": access_level}
    if password is not None:
        body["password"] = password
    resp = client.post("/users", json=body, headers=root_headers)
    assert resp.status_code == 201, resp.text
    return name


def _login(client, name, password=GOOD_PASSWORD):
    return client.post("/login/password", json={"name": name, "password": password})


class TestUserCreation:
    def test_create_with_password_reports_has_password_and_never_leaks_it(self, client, root_headers):
        name = _create_user(client, root_headers)
        listed = {u["name"]: u for u in client.get("/users", headers=root_headers).json()}
        assert listed[name]["has_password"] is True
        assert GOOD_PASSWORD not in client.get("/users", headers=root_headers).text
        assert "password_hash" not in listed[name]

    def test_create_without_password_is_token_only(self, client, root_headers):
        name = _create_user(client, root_headers, password=None)
        listed = {u["name"]: u for u in client.get("/users", headers=root_headers).json()}
        assert listed[name]["has_password"] is False

    def test_short_password_is_rejected(self, client, root_headers):
        resp = client.post(
            "/users", json={"name": _unique("pw"), "password": "short"}, headers=root_headers
        )
        assert resp.status_code == 422


class TestPasswordLogin:
    def test_success_returns_a_working_expiring_session_token(self, client, root_headers):
        name = _create_user(client, root_headers, access_level="admin")
        resp = _login(client, name)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == name
        assert data["access_level"] == "admin"
        assert data["token"].startswith("cbra_")
        assert data["expires_at"]
        # the session token authenticates like any other token (admin user -> admin route)
        assert client.get("/users", headers={"Authorization": f"Bearer {data['token']}"}).status_code == 200

    def test_wrong_password_unknown_user_and_no_password_look_identical(self, client, root_headers):
        name = _create_user(client, root_headers)
        token_only = _create_user(client, root_headers, password=None)
        responses = [
            _login(client, name, "definitely-not-it"),
            _login(client, _unique("ghost")),
            _login(client, token_only),
        ]
        assert {r.status_code for r in responses} == {401}
        assert len({r.text for r in responses}) == 1

    def test_revoked_user_cannot_log_in(self, client, root_headers):
        name = _create_user(client, root_headers)
        _sql("UPDATE cerebro_auth.users SET revoked_at = now() WHERE name = $1", name)
        assert _login(client, name).status_code == 401

    def test_expired_session_token_is_rejected(self, client, root_headers):
        name = _create_user(client, root_headers, access_level="admin")
        token = _login(client, name).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/users", headers=headers).status_code == 200
        _sql(
            "UPDATE cerebro_auth.api_tokens SET expires_at = now() - interval '1 minute' WHERE user_id = "
            "(SELECT id FROM cerebro_auth.users WHERE name = $1) AND name LIKE 'session:%'",
            name,
        )
        assert client.get("/users", headers=headers).status_code == 401


class TestLockout:
    def test_consecutive_failures_lock_the_account_even_for_the_right_password(self, client, root_headers):
        name = _create_user(client, root_headers)
        limit = get_settings().max_failed_attempts
        for _ in range(limit):
            assert _login(client, name, "wrong-password-xx").status_code == 401
        locked = _login(client, name)  # the right password, but the account is locked
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) > 0

    def test_success_resets_the_failure_counter(self, client, root_headers):
        name = _create_user(client, root_headers)
        limit = get_settings().max_failed_attempts
        for _ in range(limit - 1):
            _login(client, name, "wrong-password-xx")
        assert _login(client, name).status_code == 200
        for _ in range(limit - 1):
            assert _login(client, name, "wrong-password-xx").status_code == 401
        assert _login(client, name).status_code == 200

    def test_lock_expires(self, client, root_headers):
        name = _create_user(client, root_headers)
        for _ in range(get_settings().max_failed_attempts):
            _login(client, name, "wrong-password-xx")
        assert _login(client, name).status_code == 429
        _sql("UPDATE cerebro_auth.users SET locked_until = now() - interval '1 second' WHERE name = $1", name)
        assert _login(client, name).status_code == 200


class TestSetPassword:
    def test_admin_sets_a_password_and_old_one_and_sessions_stop_working(self, client, root_headers):
        name = _create_user(client, root_headers, access_level="admin")
        old_token = _login(client, name).json()["token"]
        resp = client.post(f"/users/{name}/password", json={"password": "a-brand-new-password"}, headers=root_headers)
        assert resp.status_code == 204
        assert _login(client, name).status_code == 401
        assert _login(client, name, "a-brand-new-password").status_code == 200
        assert client.get("/users", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401

    def test_admin_can_give_a_password_to_a_token_only_user(self, client, root_headers):
        name = _create_user(client, root_headers, password=None)
        assert _login(client, name).status_code == 401
        client.post(f"/users/{name}/password", json={"password": GOOD_PASSWORD}, headers=root_headers)
        assert _login(client, name).status_code == 200

    def test_unknown_user_is_404(self, client, root_headers):
        resp = client.post(f"/users/{_unique('ghost')}/password", json={"password": GOOD_PASSWORD}, headers=root_headers)
        assert resp.status_code == 404

    def test_user_changes_own_password_with_current_one(self, client, root_headers):
        name = _create_user(client, root_headers)
        token = _login(client, name).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            f"/users/{name}/password",
            json={"password": "another-long-password", "current_password": GOOD_PASSWORD},
            headers=headers,
        )
        assert resp.status_code == 204
        assert _login(client, name, "another-long-password").status_code == 200

    def test_user_must_supply_the_current_password(self, client, root_headers):
        name = _create_user(client, root_headers)
        headers = {"Authorization": f"Bearer {_login(client, name).json()['token']}"}
        resp = client.post(f"/users/{name}/password", json={"password": "another-long-password"}, headers=headers)
        assert resp.status_code == 422

    def test_wrong_current_password_is_refused(self, client, root_headers):
        name = _create_user(client, root_headers)
        headers = {"Authorization": f"Bearer {_login(client, name).json()['token']}"}
        resp = client.post(
            f"/users/{name}/password",
            json={"password": "another-long-password", "current_password": "not-the-current-one"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert _login(client, name).status_code == 200  # unchanged

    def test_user_cannot_change_someone_elses_password(self, client, root_headers):
        victim = _create_user(client, root_headers)
        attacker = _create_user(client, root_headers)
        headers = {"Authorization": f"Bearer {_login(client, attacker).json()['token']}"}
        resp = client.post(
            f"/users/{victim}/password",
            json={"password": "another-long-password", "current_password": GOOD_PASSWORD},
            headers=headers,
        )
        assert resp.status_code == 403
        assert _login(client, victim).status_code == 200  # unchanged


class TestLogout:
    def test_logout_revokes_the_session_token(self, client, root_headers):
        name = _create_user(client, root_headers, access_level="admin")
        headers = {"Authorization": f"Bearer {_login(client, name).json()['token']}"}
        assert client.get("/users", headers=headers).status_code == 200
        assert client.post("/logout", headers=headers).status_code == 200
        assert client.get("/users", headers=headers).status_code == 401

    def test_root_token_cannot_be_logged_out(self, client, root_headers):
        assert client.post("/logout", headers=root_headers).status_code == 400

    def test_a_non_session_token_cannot_be_logged_out(self, client, root_headers):
        name = _create_user(client, root_headers, password=None)
        created = client.post(
            "/tokens", json={"name": _unique("tok"), "scopes": ["read"], "user": name}, headers=root_headers
        ).json()
        headers = {"Authorization": f"Bearer {created['token']}"}
        assert client.post("/logout", headers=headers).status_code == 400
        # and it still works afterwards
        assert client.post("/logout", headers=headers).status_code == 400
