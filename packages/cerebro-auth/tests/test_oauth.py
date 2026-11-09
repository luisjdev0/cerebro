"""OAuth for MCP connectors: discovery, registration, sign-in page, code + PKCE exchange, refresh."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

import asyncpg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cerebro_auth import passwords
from cerebro_auth.api import create_app
from cerebro_auth.config import get_settings

PASSWORD = "master-long-password"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


def _reachable() -> bool:
    async def _c():
        conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8)
        await conn.close()

    try:
        asyncio.run(_c())
        return True
    except Exception:
        return False


@pytest.fixture
def env():
    if not _reachable():
        pytest.skip("Postgres not reachable at DATABASE_URL")
    name = f"master-{uuid.uuid4().hex[:6]}"
    settings = get_settings().model_copy(
        update={"admin_user": name, "admin_password": SecretStr(PASSWORD), "public_url": "https://cerebro.test"}
    )
    passwords._master_failures.clear()
    with TestClient(create_app(settings), follow_redirects=False) as c:
        yield c, name


def _register(c, redirect=REDIRECT):
    r = c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [redirect]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _form(c, path, data):
    return c.post(path, content=urlencode(data), headers={"Content-Type": "application/x-www-form-urlencoded"})


def _authorize(c, client_id, user, password=PASSWORD, redirect=REDIRECT):
    return _form(
        c,
        "/oauth/authorize",
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": "xyz",
            "code_challenge": CHALLENGE,
            "username": user,
            "password": password,
        },
    )


def _code(r):
    assert r.status_code == 302, r.text
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["xyz"]
    return q["code"][0]


def _exchange(c, client_id, code, verifier=VERIFIER, redirect=REDIRECT):
    return _form(
        c,
        "/oauth/token",
        {"grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": redirect, "code_verifier": verifier},
    )


def test_discovery_documents(env):
    c, _ = env
    meta = c.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["token_endpoint"] == "https://cerebro.test/api/auth/oauth/token"
    assert meta["registration_endpoint"].endswith("/api/auth/oauth/register")
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        res = c.get(path).json()
        assert res["resource"] == "https://cerebro.test/mcp"
        assert res["authorization_servers"] == ["https://cerebro.test"]


def test_any_provider_can_register_but_unsafe_redirects_are_refused(env):
    c, _ = env
    for ok in ("https://chatgpt.com/connector/oauth/abc", "http://localhost:33418/callback", "cursor://anysphere.cursor-retrieval/oauth/cb"):
        assert c.post("/oauth/register", json={"redirect_uris": [ok]}).status_code == 201, ok
    for bad in ("http://evil.example/cb", "javascript:alert(1)", "https://x.example/cb#frag", "data:text/html,x"):
        assert c.post("/oauth/register", json={"redirect_uris": [bad]}).status_code == 400, bad
    assert c.post("/oauth/register", json={}).status_code == 400


def test_the_allowlist_can_still_restrict_registration(env):
    from cerebro_auth.oauth import redirect_uri_allowed

    s = get_settings().model_copy(update={"oauth_redirect_allowlist": "https://claude.ai/"})
    assert redirect_uri_allowed(s, REDIRECT)
    assert not redirect_uri_allowed(s, "https://chatgpt.com/cb")


def test_full_flow_gives_a_working_session_and_rotating_refresh(env):
    c, user = env
    client_id = _register(c)
    page = c.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "xyz", "code_challenge": CHALLENGE, "code_challenge_method": "S256"},
    )
    assert page.status_code == 200 and "Connect to cerebro" in page.text and "claude.ai" in page.text
    assert "form-action 'self' https://claude.ai" in page.headers["content-security-policy"] and "nonce=\"" in page.text

    code = _code(_authorize(c, client_id, user))
    tok = _exchange(c, client_id, code)
    assert tok.status_code == 200, tok.text
    body = tok.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] > 3600 * 11 and body["refresh_token"]

    # The access token is a normal session: admin on the management plane.
    assert c.get("/users", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200
    # The code is single use.
    assert _exchange(c, client_id, code).json()["error"] == "invalid_grant"

    # Refresh rotates: new pair works, the old refresh token does not.
    r = _form(c, "/oauth/token", {"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": client_id})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["refresh_token"] != body["refresh_token"]
    assert c.get("/users", headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 200
    again = _form(c, "/oauth/token", {"grant_type": "refresh_token", "refresh_token": body["refresh_token"]})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_wrong_password_shows_the_form_again_and_issues_nothing(env):
    c, user = env
    client_id = _register(c)
    r = _authorize(c, client_id, user, password="nope-nope-nope")
    assert r.status_code == 401 and "Invalid username or password" in r.text
    assert "location" not in r.headers


def test_pkce_is_enforced(env):
    c, user = env
    client_id = _register(c)
    code = _code(_authorize(c, client_id, user))
    bad = _exchange(c, client_id, code, verifier="w" * 64)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    # A failed exchange also burns the code.
    assert _exchange(c, client_id, code).status_code == 400


def test_redirect_must_match_the_registered_one(env):
    c, user = env
    client_id = _register(c)
    r = c.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": client_id, "redirect_uri": "https://claude.ai/other", "code_challenge": CHALLENGE},
    )
    assert r.status_code == 400  # never redirects to an unregistered URI
    assert c.get("/oauth/authorize", params={"client_id": "nope", "redirect_uri": REDIRECT}).status_code == 400


def test_loopback_redirect_ignores_the_port(env):
    c, user = env
    client_id = _register(c, "http://localhost/callback")
    r = _authorize(c, client_id, user, redirect="http://localhost:54321/callback")
    assert r.status_code == 302 and r.headers["location"].startswith("http://localhost:54321/callback?code=")


def test_revoked_user_cannot_refresh(env):
    c, user = env
    root = {"Authorization": f"Bearer {get_settings().api_token}"}
    name = f"u-{uuid.uuid4().hex[:6]}"
    assert c.post("/users", json={"name": name, "access_level": "user"}, headers=root).status_code in (200, 201)
    assert c.post(f"/users/{name}/password", json={"password": "another-long-pass"}, headers=root).status_code == 204
    client_id = _register(c)
    code = _code(_authorize(c, client_id, name, "another-long-pass"))
    tok = _exchange(c, client_id, code).json()
    assert c.patch(f"/users/{name}", json={"active": False}, headers=root).status_code == 200
    r = _form(c, "/oauth/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    assert r.status_code == 400


def _session(c, user):
    r = c.post("/login/password", json={"name": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _confirm(c, client_id, token):
    return _form(
        c,
        "/oauth/authorize",
        {"client_id": client_id, "redirect_uri": REDIRECT, "state": "xyz", "code_challenge": CHALLENGE, "session_token": token},
    )


def test_active_portal_session_can_confirm_the_connection(env):
    c, user = env
    client_id = _register(c)
    code = _code(_confirm(c, client_id, _session(c, user)))
    tok = _exchange(c, client_id, code)
    assert tok.status_code == 200, tok.text
    assert c.get("/users", headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).status_code == 200


def test_confirm_rejects_dead_sessions_and_non_session_tokens(env):
    c, user = env
    client_id = _register(c)
    token = _session(c, user)
    assert c.post("/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    r = _confirm(c, client_id, token)  # logged out meanwhile
    assert r.status_code == 401 and "expired" in r.text and "location" not in r.headers
    # The root/service tokens are not sessions: they cannot be turned into a connector login.
    assert _confirm(c, client_id, get_settings().api_token).status_code == 401


def test_revoking_the_connections_token_also_ends_its_refresh(env):
    c, user = env
    client_id = _register(c)
    tok = _exchange(c, client_id, _code(_authorize(c, client_id, user))).json()
    # Signing out of that very session (as revoking it from the portal does) ends the connection.
    assert c.post("/logout", headers={"Authorization": f"Bearer {tok['access_token']}"}).status_code == 200
    r = _form(c, "/oauth/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": client_id})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_unused_clients_are_purged_and_the_table_is_capped(env, monkeypatch):
    from cerebro_auth import oauth

    c, _ = env
    stale = _register(c)
    asyncio.run(_exec("UPDATE oauth_clients SET created_at = now() - interval '2 days' WHERE client_id = $1", stale))
    _register(c)  # registering sweeps the stale, never-used client
    assert not asyncio.run(_fetch("SELECT 1 FROM oauth_clients WHERE client_id = $1", stale))
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 1)
    r = c.post("/oauth/register", json={"redirect_uris": [REDIRECT]})
    assert r.status_code == 503


async def _exec(query, *args):
    conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8, server_settings={"search_path": "cerebro_auth"})
    try:
        await conn.execute(query, *args)
    finally:
        await conn.close()


async def _fetch(query, *args):
    conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8, server_settings={"search_path": "cerebro_auth"})
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()
