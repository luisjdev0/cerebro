"""The same MCP server over streamable HTTP, for the gateway's `/mcp`.

stdio (`cerebro-mcp`) serves one person, with the token in the environment. This module serves
many callers from one process: it is **stateless** (no session to pin to a person) and every
request must carry its own `Authorization: Bearer <token>`, which is the token the cerebro APIs
see for that call -- so a caller can do exactly what their token allows, no more. The environment
token is deliberately ignored in this mode. A missing header is rejected here with 401 before any
MCP processing; a wrong or revoked token is rejected by the APIs, and reported by the tool.

Run:
    python -m cerebro_mcp.http          # or the `cerebro-mcp-http` entry point
    MCP_HOST (default 0.0.0.0)  MCP_PORT (default 8000)
Per-module API URLs come from CEREBRO_MEMORY_URL / CEREBRO_DOCS_URL / CEREBRO_FLOWS_URL /
CEREBRO_AUTH_URL, exactly as for stdio.
"""

from __future__ import annotations

import os
import time

import httpx
from cerebro_clients.config import auth_base_url
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cerebro_mcp import server

MCP_PATH = "/mcp"


_VALID_TTL = 30.0
_valid_until: dict[str, float] = {}


def public_base(request: Request) -> str:
    """Externally visible base URL of the gateway (CEREBRO_PUBLIC_URL, else the forwarded headers)."""
    configured = os.environ.get("CEREBRO_PUBLIC_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


async def token_is_rejected(token: str) -> bool:
    """True only when cerebro-auth positively says the token is invalid, revoked or expired.

    An expired access token has to be a *HTTP* 401 on `/mcp` -- that is what makes a connector
    use its refresh token -- instead of a tool-level error the connector would never act on.
    If the auth service cannot be reached the request goes on: the APIs have the last word.
    """
    now = time.monotonic()
    if _valid_until.get(token, 0.0) > now:
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.post(f"{auth_base_url().rstrip('/')}/login", json={"token": token})
    except httpx.HTTPError:
        return False
    if r.status_code == 401:
        _valid_until.pop(token, None)
        return True
    if r.status_code == 200:
        if len(_valid_until) > 512:
            _valid_until.clear()
        _valid_until[token] = now + _VALID_TTL
    return False


class RequireBearerMiddleware(BaseHTTPMiddleware):
    """401 for any `/mcp` request without a (valid) Bearer token.

    The `WWW-Authenticate` header points to the protected-resource metadata, which is what
    sends a connector (claude.ai, Claude Desktop) to the sign-in page. Callers that can set
    headers (Claude Code, scripts) keep sending a cerebro token directly.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith(MCP_PATH):
            metadata = f"{public_base(request)}/.well-known/oauth-protected-resource{MCP_PATH}"
            value = request.headers.get("authorization", "")
            token = value[7:].strip() if value.lower().startswith("bearer ") else ""
            if not token:
                return JSONResponse(
                    {"error": "missing bearer token: sign in through the connector, or send `Authorization: Bearer <your cerebro token>`"},
                    status_code=401,
                    headers={"WWW-Authenticate": f'Bearer realm="cerebro", resource_metadata="{metadata}"'},
                )
            if await token_is_rejected(token):
                return JSONResponse(
                    {"error": "invalid, revoked or expired token"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer realm="cerebro", error="invalid_token", resource_metadata="{metadata}"'
                    },
                )
        return await call_next(request)


def create_app() -> Starlette:
    server.HTTP_MODE = True
    settings = server.mcp.settings
    settings.stateless_http = True
    settings.json_response = True
    settings.streamable_http_path = MCP_PATH
    # The gateway/reverse proxy forwards arbitrary Host headers; DNS-rebinding protection is for
    # servers bound to localhost, and every call here already needs a bearer token.
    settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    @server.mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    app = server.mcp.streamable_http_app()
    app.add_middleware(RequireBearerMiddleware)
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
