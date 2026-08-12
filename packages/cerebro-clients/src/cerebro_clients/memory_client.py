"""`MemoryClient`: wrapper 1:1 sobre la API HTTP de cerebro-memory.

Extraido de las llamadas httpx que antes vivian repartidas entre
`cerebro_memory.mcp_server` y `cerebro_memory.cli` -- sin ninguna logica de negocio
propia (ni validacion de `type`, ni el aprendizaje de desambiguacion "ultima
ambigua -> siguiente resolucion", ni el formateo de mensajes para un LLM: todo eso es
presentacion y vive en `cerebro-mcp`/`cerebro-cli`). Cada metodo hace una request y
devuelve el JSON ya decodificado (`response.json()`), o deja propagar
`CerebroAPIError`/`CerebroConnectionError` (ver `base.py`).
"""

from __future__ import annotations

from typing import Any

import httpx

from cerebro_clients.base import BaseClient
from cerebro_clients.config import agent_name, memory_base_url, memory_token


class MemoryClient(BaseClient):
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        agent: str | None = None,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(
            base_url or memory_base_url(),
            token if token is not None else memory_token(),
            agent or agent_name(),
            timeout=timeout,
            transport=transport,
        )

    # --------------------------------------------------------------------- health

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").json()

    # --------------------------------------------------------------------- memories

    def search_memories(
        self,
        query: str,
        *,
        context: str | None = None,
        scope: str | None = None,
        type: str | None = None,  # noqa: A002 - alineado con la API
        limit: int = 5,
        include_superseded: bool = False,
        expand: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "limit": limit}
        if context is not None:
            params["context"] = context
        if scope is not None:
            params["scope"] = scope
        if type is not None:
            params["type"] = type
        if include_superseded:
            params["include_superseded"] = True
        if expand:
            params["expand"] = True
        return self._request("GET", "/memories/search", params=params).json()

    def create_memory(
        self,
        content: str,
        context: str,
        type: str,  # noqa: A002
        *,
        title: str | None = None,
        importance: float | None = None,
        occurred_at: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content, "context": context, "type": type}
        if title is not None:
            body["title"] = title
        if importance is not None:
            body["importance"] = importance
        if occurred_at is not None:
            body["occurred_at"] = occurred_at
        if source is not None:
            body["source"] = source
        return self._request("POST", "/memories", json=body).json()

    def update_memory(self, memory_id: str, content: str) -> dict[str, Any]:
        return self._request("PATCH", f"/memories/{memory_id}", json={"content": content}).json()

    def delete_memory(self, memory_id: str, *, hard: bool = False) -> dict[str, Any]:
        return self._request("DELETE", f"/memories/{memory_id}", params={"hard": hard}).json()

    # --------------------------------------------------------------------- Context Engine

    def resolve_disambiguation(self, disambiguation_id: str, context: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/disambiguations/{disambiguation_id}/resolve", json={"context": context}
        ).json()

    def export_disambiguations(self, *, resolved_only: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if resolved_only:
            params["resolved_only"] = True
        return self._request("GET", "/disambiguations/export", params=params).json()

    # --------------------------------------------------------------------- grafo (Fase 3)

    def create_edge(
        self, from_memory_id: str, to_memory_id: str, relation: str, *, note: str | None = None
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/memories/{from_memory_id}/edges",
            json={"to_memory": to_memory_id, "relation": relation, "note": note},
        ).json()

    def delete_edge(self, memory_id: str, edge_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/memories/{memory_id}/edges/{edge_id}").json()

    def get_related(self, memory_id: str, *, relation: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if relation is not None:
            params["relation"] = relation
        return self._request("GET", f"/memories/{memory_id}/related", params=params).json()

    def get_timeline(
        self,
        *,
        context: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if context is not None:
            params["context"] = context
        if from_date is not None:
            params["from"] = from_date
        if to_date is not None:
            params["to"] = to_date
        return self._request("GET", "/timeline", params=params).json()

    # --------------------------------------------------------------------- contexts

    def list_contexts(self) -> list[dict[str, Any]]:
        return self._request("GET", "/contexts").json()

    def create_context(self, slug: str, name: str, kind: str, *, description: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"slug": slug, "name": name, "kind": kind}
        if description is not None:
            body["description"] = description
        return self._request("POST", "/contexts", json=body).json()

    def delete_context(self, slug: str, *, force: bool = False) -> dict[str, Any]:
        return self._request("DELETE", f"/contexts/{slug}", params={"force": force}).json()

    # --------------------------------------------------------------------- stats

    def get_stats(self) -> dict[str, Any]:
        return self._request("GET", "/stats").json()

    # --------------------------------------------------------------------- tokens

    def create_token(
        self,
        name: str,
        scopes: list[str],
        *,
        allowed_contexts: list[str] | None = None,
        value: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "scopes": scopes}
        if allowed_contexts is not None:
            body["allowed_contexts"] = allowed_contexts
        if value is not None:
            body["value"] = value
        return self._request("POST", "/tokens", json=body).json()

    def list_tokens(self) -> list[dict[str, Any]]:
        return self._request("GET", "/tokens").json()

    def revoke_token(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/tokens/{name}").json()
