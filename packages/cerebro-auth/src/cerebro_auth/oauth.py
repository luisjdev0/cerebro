"""OAuth 2.1 authorization server for MCP connectors (claude.ai, Claude Desktop, Claude Code).

A connector cannot be handed a header: adding it must open a sign-in page and, once the person
has authenticated, register the token on its own. That is the OAuth authorization-code flow with
PKCE, which is all this module implements:

    1. the client discovers us through `/.well-known/oauth-protected-resource` (pointed to by the
       `WWW-Authenticate` of a 401 from `/mcp`) and `/.well-known/oauth-authorization-server`;
    2. it registers itself (`POST /oauth/register`, RFC 7591) -- public client, no secret;
    3. it sends the browser to `GET /oauth/authorize`, which shows the sign-in form; the person
       signs in with the same username + password as the portal (database users or the master);
    4. the browser goes back to the client's redirect URI with a one-shot `code`;
    5. the client trades it, with its PKCE verifier, at `POST /oauth/token` for an access token --
       an ordinary 12 h *session token* (see `passwords.py`), so every service validates it like
       any other -- and a rotating refresh token.

Passwords are checked by the same code as `POST /login/password` (lockout included). Codes and
refresh tokens are stored hashed. Any client may register (the point is that every MCP provider can connect); the sign-in page
names the host the browser will return to, and `OAUTH_REDIRECT_ALLOWLIST` can restrict it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from cerebro_auth.auth import hash_token, resolve_effective_scope
from cerebro_auth.config import Settings
from cerebro_auth.passwords import (
    SESSION_PREFIX,
    AccountLockedError,
    InvalidCredentialsError,
    authenticate_password,
    is_master_name,
    mint_session,
)

CODE_TTL_SECONDS = 60
MCP_PATH = "/mcp"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


# --------------------------------------------------------------------------- helpers


def public_base(request: Request, settings: Settings) -> str:
    """Externally visible base URL of the gateway (what clients must be told)."""
    if settings.public_url.strip():
        return settings.public_url.strip().rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


def _api_base(request: Request, settings: Settings) -> str:
    # The gateway exposes this service under /api/auth (the prefix is stripped before it gets here).
    return f"{public_base(request, settings)}/api/auth"


def _allowlist(settings: Settings) -> list[str]:
    return [p.strip() for p in settings.oauth_redirect_allowlist.split(",") if p.strip()]


def _is_loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK_HOSTS


_FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob", "ftp", "ws", "wss"}


def redirect_uri_allowed(settings: Settings, uri: str) -> bool:
    """Any MCP client may register (the connection is meant to work with every provider), as
    long as its redirect is one a browser can safely hand a code to: https, a loopback address
    over http (CLI tools, RFC 8252), or an app's own scheme (`cursor://`, `vscode://`...).
    `OAUTH_REDIRECT_ALLOWLIST` (comma-separated prefixes), when set, narrows that further."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not scheme or parts.fragment or scheme in _FORBIDDEN_SCHEMES:
        return False
    if scheme in ("http", "https"):
        if not parts.netloc or (scheme == "http" and not _is_loopback(uri)):
            return False
    allow = _allowlist(settings)
    return not allow or any(uri.startswith(prefix) for prefix in allow)


def _same_redirect(registered: str, requested: str) -> bool:
    """Exact match, except that loopback redirects may differ in port (RFC 8252 section 7.3)."""
    if registered == requested:
        return True
    if _is_loopback(registered) and _is_loopback(requested):
        a, b = urlsplit(registered), urlsplit(requested)
        return (a.hostname, a.path, a.query) == (b.hostname, b.path, b.query)
    return False


def _verify_pkce(verifier: str, challenge: str) -> bool:
    if not (43 <= len(verifier) <= 128):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


def _json(data: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _oauth_error(error: str, description: str = "", status: int = 400) -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return _json(body, status)


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


_LOGO = (
    '<svg viewBox="0 0 96 96" width="44" height="44" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#60a5fa"/><stop offset="1" stop-color="#2563eb"/></linearGradient></defs>'
    '<rect width="96" height="96" rx="24" fill="#0f172a"/><g fill="none" stroke="url(#g)" stroke-width="5" stroke-linecap="round">'
    '<path d="M48 24v48M30 34c-8 4-8 16 0 20M66 34c8 4 8 16 0 20M30 54c-6 6-2 16 8 16M66 54c6 6 2 16-8 16M38 24c-6 0-10 4-10 10M58 24c6 0 10 4 10 10"/></g>'
    '<circle cx="48" cy="48" r="5" fill="#60a5fa"/></svg>'
)

_STYLE = """
:root{--bg:#f1f5f9;--card:#fff;--fg:#0f172a;--muted:#64748b;--border:#e2e8f0;--brand:#2563eb;--brand-fg:#fff;--field:#fff;--danger:#dc2626;--danger-bg:#fef2f2}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--card:#111a2e;--fg:#cdd6e2;--muted:#94a3b8;--border:#233049;--brand:#3b82f6;--field:#0b1220;--danger:#f87171;--danger-bg:#2a1518}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;background:radial-gradient(900px 500px at 15% -10%,#2563eb22,transparent),radial-gradient(700px 400px at 110% 110%,#60a5fa1f,transparent),var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.card{width:100%;max-width:380px;background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px 26px;box-shadow:0 10px 30px #0f172a14}
.brand{display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px;margin-bottom:20px}
.brand h1{margin:0;font-size:1.2rem;font-weight:650}
.brand p{margin:0;color:var(--muted);font-size:.86rem}
.brand b{color:var(--fg);font-weight:600}
label{display:block;font-size:.8rem;font-weight:550;margin-bottom:14px}
input[type=text],input[type=password]{display:block;width:100%;margin-top:6px;padding:.62rem .75rem;font:inherit;color:var(--fg);background:var(--field);border:1px solid var(--border);border-radius:10px;outline:none}
input:focus{border-color:var(--brand);box-shadow:0 0 0 3px #2563eb33}
button{display:block;width:100%;padding:.65rem;font:inherit;font-weight:600;border:0;border-radius:10px;background:var(--brand);color:var(--brand-fg);cursor:pointer}
button:hover{filter:brightness(1.08)}
button.link{background:none;color:var(--muted);font-weight:500;font-size:.84rem;margin-top:10px;padding:.35rem}
button.link:hover{color:var(--fg);filter:none}
.err{margin:0 0 14px;padding:.55rem .75rem;font-size:.84rem;color:var(--danger);background:var(--danger-bg);border-radius:10px}
.who{display:flex;align-items:center;gap:12px;padding:12px;margin-bottom:14px;border:1px solid var(--border);border-radius:12px}
.avatar{flex:none;width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:var(--brand);color:var(--brand-fg);font-weight:650;text-transform:uppercase}
.who small{display:block;color:var(--muted);font-size:.74rem}
.who strong{font-size:.95rem;word-break:break-all}
.note{margin:14px 0 0;text-align:center;color:var(--muted);font-size:.74rem}
[hidden]{display:none!important}
"""

_SCRIPT = """
(function(){
  var KEY='cerebro.session', root=document.getElementById('c');
  var confirmBox=document.getElementById('confirm'), form=document.getElementById('signin');
  function show(which){confirmBox.hidden=which!=='confirm';form.hidden=which!=='signin';
    var f=form.querySelector('input[name=username]'); if(which==='signin'&&f)f.focus();}
  var s=null;
  try{ if(root.dataset.ignore!=='1'){ s=JSON.parse(localStorage.getItem(KEY)||'null'); } }catch(e){s=null}
  if(s&&s.token&&s.name&&Date.parse(s.expires_at)>Date.now()){
    document.getElementById('who').textContent=s.name;
    document.getElementById('avatar').textContent=s.name.charAt(0);
    confirmBox.querySelector('input[name=session_token]').value=s.token;
    show('confirm');
  } else { show('signin'); }
  document.getElementById('other').addEventListener('click',function(){
    confirmBox.querySelector('input[name=session_token]').value=''; show('signin'); });
})();
"""


def _login_page(
    client_name: str, redirect_host: str, fields: dict[str, str], nonce: str, error: str = "", ignore_session: bool = False
) -> str:
    hidden = "".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(v, quote=True)}">' for k, v in fields.items()
    )
    err = f'<p class="err" role="alert">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Connect to cerebro</title>
<style nonce="{nonce}">{_STYLE}</style></head>
<body><main class="card" id="c" data-ignore="{1 if ignore_session else 0}">
<div class="brand">{_LOGO}<h1>Connect to cerebro</h1>
<p><b>{escape(client_name)}</b> (returns to <b>{escape(redirect_host)}</b>) wants to use your memory, documents and flows.</p></div>
{err}
<form method="post" id="confirm" hidden>{hidden}
<input type="hidden" name="session_token" value="">
<div class="who"><span class="avatar" id="avatar"></span><div><small>Signed in as</small><strong id="who"></strong></div></div>
<button type="submit">Confirm connection</button>
<button type="button" class="link" id="other">Use another account</button>
</form>
<form method="post" id="signin">{hidden}
<label>Username<input type="text" name="username" autocomplete="username" required></label>
<label>Password<input type="password" name="password" autocomplete="current-password" required></label>
<button type="submit">Sign in and connect</button>
</form>
<p class="note">You can revoke this connection at any time from the portal.</p>
</main><script nonce="{nonce}">{_SCRIPT}</script></body></html>"""


def _page(html: str, redirect_uri: str, nonce: str, status: int = 200) -> HTMLResponse:
    # Browsers apply `form-action` to the redirect that follows the form post as well, so the
    # (already validated) origin of the client's redirect URI has to be allowed explicitly.
    parts = urlsplit(redirect_uri)
    origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") else f"{parts.scheme}:"
    return HTMLResponse(
        html,
        status_code=status,
        headers={
            "Content-Security-Policy": (
                f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                f"form-action 'self' {origin}; frame-ancestors 'none'; base-uri 'none'"
            ),
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
        },
    )


async def session_identity(pool: asyncpg.Pool, token: str) -> tuple[str, bool] | None:
    """Who a *portal session token* belongs to, as `(user_name, is_master)`; `None` if the token
    is not a live session. Only session tokens qualify -- a service token must not be turned into
    a login by pasting it into this page."""
    if not token:
        return None
    resolved = await resolve_effective_scope(pool, hash_token(token))
    if resolved is None:
        return None
    row = await pool.fetchrow(
        "SELECT name, user_id FROM api_tokens WHERE token_hash = $1 AND name LIKE $2",
        hash_token(token),
        f"{SESSION_PREFIX}%",
    )
    if row is None:
        return None
    if row["user_id"] is not None:
        return resolved.name, False
    # The master has no user row: the name is embedded in the token's own (session:<user>:<hex>).
    return row["name"][len(SESSION_PREFIX) :].rsplit(":", 1)[0], True


# --------------------------------------------------------------------------- router


def build_router(get_pool: Any, settings: Settings) -> APIRouter:
    router = APIRouter()

    def pool_of(request: Request) -> asyncpg.Pool:
        return get_pool(request)

    # ------------------------------------------------------------ discovery

    @router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    @router.get("/.well-known/oauth-authorization-server/{rest:path}", include_in_schema=False)
    async def as_metadata(request: Request, rest: str = ""):
        base, api = public_base(request, settings), _api_base(request, settings)
        return _json(
            {
                "issuer": base,
                "authorization_endpoint": f"{api}/oauth/authorize",
                "token_endpoint": f"{api}/oauth/token",
                "registration_endpoint": f"{api}/oauth/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    @router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @router.get("/.well-known/oauth-protected-resource/{rest:path}", include_in_schema=False)
    async def resource_metadata(request: Request, rest: str = ""):
        base = public_base(request, settings)
        return _json(
            {
                "resource": f"{base}{MCP_PATH}",
                "authorization_servers": [base],
                "bearer_methods_supported": ["header"],
            }
        )

    # ------------------------------------------------------------ registration

    @router.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
        try:
            body = json.loads((await request.body()) or b"{}")
        except ValueError:
            return _oauth_error("invalid_client_metadata", "body must be JSON")
        uris = body.get("redirect_uris") if isinstance(body, dict) else None
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            return _oauth_error("invalid_redirect_uri", "redirect_uris is required")
        bad = [u for u in uris if not redirect_uri_allowed(settings, u)]
        if bad:
            return _oauth_error("invalid_redirect_uri", f"redirect URI not allowed: {bad[0]}")
        name = str(body.get("client_name") or "MCP client")[:100]
        client_id = secrets.token_urlsafe(24)
        await pool_of(request).execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES ($1, $2, $3::jsonb)",
            client_id,
            name,
            json.dumps(uris),
        )
        return _json(
            {
                "client_id": client_id,
                "client_name": name,
                "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
            201,
        )

    # ------------------------------------------------------------ authorize

    async def _load_client(pool: asyncpg.Pool, client_id: str) -> dict[str, Any] | None:
        row = await pool.fetchrow(
            "SELECT client_id, client_name, redirect_uris FROM oauth_clients WHERE client_id = $1", client_id
        )
        if row is None:
            return None
        uris = row["redirect_uris"]
        if isinstance(uris, str):  # asyncpg does not decode jsonb
            uris = json.loads(uris)
        return {"client_id": row["client_id"], "client_name": row["client_name"] or "MCP client", "redirect_uris": uris}

    def _validate(params: dict[str, str], client: dict[str, Any] | None) -> str | None:
        if client is None:
            return "unknown client_id"
        if not any(_same_redirect(r, params.get("redirect_uri", "")) for r in client["redirect_uris"]):
            return "redirect_uri does not match the registered one"
        return None

    def _redirect_back(redirect_uri: str, **query: str) -> RedirectResponse:
        parts = urlsplit(redirect_uri)
        existing = parts.query + "&" if parts.query else ""
        url = urlunsplit(parts._replace(query=existing + urlencode({k: v for k, v in query.items() if v})))
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})

    @router.get("/oauth/authorize", include_in_schema=False)
    async def authorize_form(request: Request):
        q = dict(request.query_params)
        client = await _load_client(pool_of(request), q.get("client_id", ""))
        problem = _validate(q, client)
        if problem:  # never redirect to an unvalidated URI
            return _oauth_error("invalid_request", problem)
        if q.get("response_type") != "code":
            return _redirect_back(q["redirect_uri"], error="unsupported_response_type", state=q.get("state", ""))
        if not q.get("code_challenge") or q.get("code_challenge_method", "S256") != "S256":
            return _redirect_back(
                q["redirect_uri"], error="invalid_request", error_description="PKCE with S256 is required", state=q.get("state", "")
            )
        fields = {k: q.get(k, "") for k in ("client_id", "redirect_uri", "state", "code_challenge")}
        host = urlsplit(q["redirect_uri"]).netloc
        nonce = secrets.token_urlsafe(16)
        return _page(_login_page(client["client_name"], host, fields, nonce), q["redirect_uri"], nonce)

    @router.post("/oauth/authorize", include_in_schema=False)
    async def authorize_submit(request: Request):
        pool = pool_of(request)
        f = await _form(request)
        client = await _load_client(pool, f.get("client_id", ""))
        problem = _validate(f, client)
        if problem or not f.get("code_challenge"):
            return _oauth_error("invalid_request", problem or "missing code_challenge")
        fields = {k: f.get(k, "") for k in ("client_id", "redirect_uri", "state", "code_challenge")}
        host = urlsplit(f["redirect_uri"]).netloc

        def again(message: str, status: int, ignore_session: bool) -> HTMLResponse:
            nonce = secrets.token_urlsafe(16)
            html = _login_page(client["client_name"], host, fields, nonce, message, ignore_session)
            return _page(html, f["redirect_uri"], nonce, status)

        if f.get("session_token"):
            # "Confirm connection": the person is already signed in to the portal (same origin).
            identity = await session_identity(pool, f["session_token"])
            if identity is None:
                return again("Your session has expired. Please sign in again.", 401, True)
            user_name, master = identity
        else:
            try:
                row, master = await authenticate_password(pool, settings, f.get("username", ""), f.get("password", ""))
            except AccountLockedError as exc:
                return again(f"Too many failed attempts, try again in {exc.retry_after} seconds.", 429, True)
            except InvalidCredentialsError:
                return again("Invalid username or password.", 401, True)
            user_name = row["name"]

        code = secrets.token_urlsafe(32)
        await pool.execute("DELETE FROM oauth_codes WHERE expires_at < now()")
        await pool.execute(
            """
            INSERT INTO oauth_codes (code_hash, client_id, redirect_uri, code_challenge, user_name, is_master, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, now() + make_interval(secs => $7))
            """,
            hash_token(code),
            client["client_id"],
            f["redirect_uri"],
            f["code_challenge"],
            user_name,
            master,
            float(CODE_TTL_SECONDS),
        )
        return _redirect_back(f["redirect_uri"], code=code, state=f.get("state", ""))

    # ------------------------------------------------------------ token

    async def _issue(pool: asyncpg.Pool, client_id: str, user_name: str, master: bool) -> JSONResponse | None:
        if master:
            if not is_master_name(settings, user_name):
                return None
            row = {"id": None, "name": user_name, "access_level": "admin"}
        else:
            row = await pool.fetchrow(
                "SELECT id, name, access_level FROM users WHERE name = $1 AND revoked_at IS NULL", user_name
            )
            if row is None:
                return None
        session = await mint_session(pool, settings, row, master)
        refresh = secrets.token_urlsafe(48)
        await pool.execute("DELETE FROM oauth_refresh_tokens WHERE expires_at < now()")
        await pool.execute(
            """
            INSERT INTO oauth_refresh_tokens (token_hash, client_id, user_name, is_master, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            hash_token(refresh),
            client_id,
            user_name,
            master,
            datetime.now(timezone.utc) + timedelta(days=settings.oauth_refresh_days),
        )
        expires_in = max(1, int((session["expires_at"] - datetime.now(timezone.utc)).total_seconds()))
        return _json(
            {
                "access_token": session["token"],
                "token_type": "Bearer",
                "expires_in": expires_in,
                "refresh_token": refresh,
            }
        )

    @router.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        pool = pool_of(request)
        f = await _form(request)
        grant = f.get("grant_type")

        if grant == "authorization_code":
            # Single use: the delete is what consumes the code.
            row = await pool.fetchrow(
                """
                DELETE FROM oauth_codes WHERE code_hash = $1 AND expires_at > now()
                RETURNING client_id, redirect_uri, code_challenge, user_name, is_master
                """,
                hash_token(f.get("code", "")),
            )
            if row is None:
                return _oauth_error("invalid_grant", "code is invalid, expired or already used")
            if row["client_id"] != f.get("client_id") or row["redirect_uri"] != f.get("redirect_uri"):
                return _oauth_error("invalid_grant", "client_id or redirect_uri does not match the authorization request")
            if not _verify_pkce(f.get("code_verifier", ""), row["code_challenge"]):
                return _oauth_error("invalid_grant", "code_verifier does not match the code_challenge")
            issued = await _issue(pool, row["client_id"], row["user_name"], row["is_master"])
            return issued or _oauth_error("invalid_grant", "the account is no longer valid")

        if grant == "refresh_token":
            # Rotation: the old refresh token dies with the exchange.
            row = await pool.fetchrow(
                """
                DELETE FROM oauth_refresh_tokens WHERE token_hash = $1 AND expires_at > now()
                RETURNING client_id, user_name, is_master
                """,
                hash_token(f.get("refresh_token", "")),
            )
            if row is None:
                return _oauth_error("invalid_grant", "refresh token is invalid or expired")
            if f.get("client_id") and f["client_id"] != row["client_id"]:
                return _oauth_error("invalid_grant", "refresh token was issued to another client")
            issued = await _issue(pool, row["client_id"], row["user_name"], row["is_master"])
            return issued or _oauth_error("invalid_grant", "the account is no longer valid")

        return _oauth_error("unsupported_grant_type")

    return router
