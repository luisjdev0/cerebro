"""Configuration resolution via environment variables, shared by `MemoryClient`
and `DocsClient` (ecosistema-cerebro.md SS4 and SS13 "cross-cutting tokens").

Precedence:
    URL    -- CEREBRO_MEMORY_URL / CEREBRO_DOCS_URL  >  (memory only) KNOWLEDGEOS_API_URL  >  local default
    Token  -- CEREBRO_TOKEN                          >  (memory only) KNOWLEDGEOS_API_TOKEN >  "" (no auth)
    Agent  -- CEREBRO_AGENT_NAME                      >  (memory only) KNOWLEDGEOS_AGENT_NAME >  default

The KNOWLEDGEOS_* fallback only applies to memory: those are the variables the
user's environment already had configured before this layer existed (cerebro-memory's
mcp_server.py/cli.py read them directly), and the plan explicitly asks not to break them.
cerebro-docs is a new service, with no legacy variables to preserve.
"""

from __future__ import annotations

import os

DEFAULT_MEMORY_URL = "http://localhost:8005"
DEFAULT_DOCS_URL = "http://localhost:8010"
DEFAULT_FLOWS_URL = "http://localhost:8020"
DEFAULT_AGENT_NAME = "cerebro-client"


def memory_base_url() -> str:
    return os.environ.get("CEREBRO_MEMORY_URL") or os.environ.get("KNOWLEDGEOS_API_URL") or DEFAULT_MEMORY_URL


def docs_base_url() -> str:
    return os.environ.get("CEREBRO_DOCS_URL") or DEFAULT_DOCS_URL


def memory_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or os.environ.get("KNOWLEDGEOS_API_TOKEN") or ""


def docs_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or ""


def flows_base_url() -> str:
    return os.environ.get("CEREBRO_FLOWS_URL") or DEFAULT_FLOWS_URL


def flows_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or ""


def agent_name() -> str:
    return os.environ.get("CEREBRO_AGENT_NAME") or os.environ.get("KNOWLEDGEOS_AGENT_NAME") or DEFAULT_AGENT_NAME
