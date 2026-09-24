"""Configuration resolution via environment variables, shared by `MemoryClient`,
`DocsClient`, `FlowsClient`, and `AuthClient` (ecosistema-cerebro.md SS4 and SS13
"cross-cutting tokens").

Precedence:
    URL    -- CEREBRO_MEMORY_URL / CEREBRO_DOCS_URL / CEREBRO_FLOWS_URL / CEREBRO_AUTH_URL
              >  (memory only) KNOWLEDGEOS_API_URL
              >  ~/.cerebro/config.json's "url" (the gateway base saved by `cerebro login`) + "/<module>"
              >  local default
    Token  -- CEREBRO_TOKEN                          >  (memory only) KNOWLEDGEOS_API_TOKEN
              >  ~/.cerebro/config.json's "token" (saved by `cerebro login`)
              >  "" (no auth)
    Agent  -- CEREBRO_AGENT_NAME                      >  (memory only) KNOWLEDGEOS_AGENT_NAME >  default

The KNOWLEDGEOS_* fallback only applies to memory: those are the variables the
user's environment already had configured before this layer existed (cerebro-memory's
mcp_server.py/cli.py read them directly), and the plan explicitly asks not to break them.
cerebro-docs, cerebro-flows, and cerebro-auth are newer services, with no legacy
variables to preserve.

Local-dev default ports (non-Docker: `python -m cerebro_<x>.main`) follow each
service's own `app_port` default -- memory=8000 (legacy-special, see below), docs=8010,
flows=8020, auth=8030 -- not the Docker Compose host ports (compose.yaml maps
memory/docs/flows/auth to 8005/8006/8007/8008, a separate, incidental numbering used
only for the container port mapping).

`~/.cerebro/config.json` is written by `cerebro login` (`cerebro_cli.tokens`), never
by this package -- its "url" field is the GATEWAY's base URL (e.g.
https://cerebro.example.com), not any single service's own address, since that's what
a human types once at login time. Each `*_base_url()` below appends its own
`/<module>` prefix to it, matching the gateway's routing convention
(gateway/Caddyfile) -- the same convention `CEREBRO_MEMORY_URL=.../memory` already
follows when set explicitly via environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MEMORY_URL = "http://localhost:8005"
DEFAULT_DOCS_URL = "http://localhost:8010"
DEFAULT_FLOWS_URL = "http://localhost:8020"
DEFAULT_AUTH_URL = "http://localhost:8030"
DEFAULT_AGENT_NAME = "cerebro-client"

_CONFIG_PATH = Path.home() / ".cerebro" / "config.json"


def _load_login_config() -> dict[str, Any] | None:
    """Best-effort read of `~/.cerebro/config.json` -- missing, unreadable, or
    malformed is treated the same as "no saved login" (falls through to the next
    precedence level), never an error: this file is a convenience, not a
    requirement, every function here already works fine without it via env vars."""
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _saved_token() -> str | None:
    config = _load_login_config()
    return config.get("token") if config else None


def _saved_module_url(module: str) -> str | None:
    config = _load_login_config()
    gateway_url = config.get("url") if config else None
    if not gateway_url:
        return None
    return f"{gateway_url.rstrip('/')}/{module}"


def memory_base_url() -> str:
    return (
        os.environ.get("CEREBRO_MEMORY_URL")
        or os.environ.get("KNOWLEDGEOS_API_URL")
        or _saved_module_url("memory")
        or DEFAULT_MEMORY_URL
    )


def docs_base_url() -> str:
    return os.environ.get("CEREBRO_DOCS_URL") or _saved_module_url("docs") or DEFAULT_DOCS_URL


def memory_token() -> str:
    return (
        os.environ.get("CEREBRO_TOKEN")
        or os.environ.get("KNOWLEDGEOS_API_TOKEN")
        or _saved_token()
        or ""
    )


def docs_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or _saved_token() or ""


def flows_base_url() -> str:
    return os.environ.get("CEREBRO_FLOWS_URL") or _saved_module_url("flows") or DEFAULT_FLOWS_URL


def flows_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or _saved_token() or ""


def auth_base_url() -> str:
    return os.environ.get("CEREBRO_AUTH_URL") or _saved_module_url("auth") or DEFAULT_AUTH_URL


def auth_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or _saved_token() or ""


def agent_name() -> str:
    return os.environ.get("CEREBRO_AGENT_NAME") or os.environ.get("KNOWLEDGEOS_AGENT_NAME") or DEFAULT_AGENT_NAME
