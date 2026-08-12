"""Resolucion de configuracion por variables de entorno, compartida por `MemoryClient`
y `DocsClient` (ecosistema-cerebro.md SS4 y SS13 "tokens transversales").

Precedencia:
    URL    -- CEREBRO_MEMORY_URL / CEREBRO_DOCS_URL  >  (solo memory) KNOWLEDGEOS_API_URL  >  default local
    Token  -- CEREBRO_TOKEN                          >  (solo memory) KNOWLEDGEOS_API_TOKEN >  "" (sin auth)
    Agente -- CEREBRO_AGENT_NAME                      >  (solo memory) KNOWLEDGEOS_AGENT_NAME >  default

El fallback KNOWLEDGEOS_* solo aplica a memory: son las variables que el entorno del
usuario ya tenia configuradas antes de esta capa (mcp_server.py/cli.py de
cerebro-memory las leian directamente) y el plan explicitamente pide no romperlas.
cerebro-docs es un servicio nuevo, sin variables legadas que preservar.
"""

from __future__ import annotations

import os

DEFAULT_MEMORY_URL = "http://localhost:8005"
DEFAULT_DOCS_URL = "http://localhost:8010"
DEFAULT_AGENT_NAME = "cerebro-client"


def memory_base_url() -> str:
    return os.environ.get("CEREBRO_MEMORY_URL") or os.environ.get("KNOWLEDGEOS_API_URL") or DEFAULT_MEMORY_URL


def docs_base_url() -> str:
    return os.environ.get("CEREBRO_DOCS_URL") or DEFAULT_DOCS_URL


def memory_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or os.environ.get("KNOWLEDGEOS_API_TOKEN") or ""


def docs_token() -> str:
    return os.environ.get("CEREBRO_TOKEN") or ""


def agent_name() -> str:
    return os.environ.get("CEREBRO_AGENT_NAME") or os.environ.get("KNOWLEDGEOS_AGENT_NAME") or DEFAULT_AGENT_NAME
