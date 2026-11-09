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

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cerebro_mcp import server

MCP_PATH = "/mcp"


class RequireBearerMiddleware(BaseHTTPMiddleware):
    """401 for any `/mcp` request that carries no Bearer token (the APIs do the real check)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith(MCP_PATH):
            value = request.headers.get("authorization", "")
            if not (value.lower().startswith("bearer ") and value[7:].strip()):
                return JSONResponse(
                    {"error": "missing bearer token: send `Authorization: Bearer <your cerebro token>`"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="cerebro"'},
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
