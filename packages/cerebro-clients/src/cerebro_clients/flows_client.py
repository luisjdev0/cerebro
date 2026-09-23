"""`FlowsClient`: wrapper 1:1 sobre la API HTTP de cerebro-flows.

Mismo criterio que `DocsClient`/`MemoryClient` (ver sus docstrings): cero logica de
negocio, cada metodo hace una request y devuelve el JSON decodificado o deja
propagar `CerebroAPIError`/`CerebroConnectionError`.
"""

from __future__ import annotations

from typing import Any

import httpx

from cerebro_clients.base import BaseClient
from cerebro_clients.config import agent_name, flows_base_url, flows_token


class FlowsClient(BaseClient):
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
            base_url or flows_base_url(),
            token if token is not None else flows_token(),
            agent or agent_name(),
            timeout=timeout,
            transport=transport,
        )

    # --------------------------------------------------------------------- health

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").json()

    # --------------------------------------------------------------------- categories

    def create_category(self, slug: str, code: str, name: str, *, description: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"slug": slug, "code": code, "name": name}
        if description is not None:
            body["description"] = description
        return self._request("POST", "/categories", json=body).json()

    def list_categories(self) -> list[dict[str, Any]]:
        return self._request("GET", "/categories").json()

    # --------------------------------------------------------------------- flow definitions

    def validate_flow(self, yaml_content: str) -> dict[str, Any]:
        return self._request("POST", "/flows/validate", json={"yaml_content": yaml_content}).json()

    def create_flow(self, category: str, yaml_content: str, *, code: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"category": category, "yaml_content": yaml_content}
        if code is not None:
            body["code"] = code
        return self._request("POST", "/flows", json=body).json()

    def get_flow(self, code: str) -> dict[str, Any]:
        return self._request("GET", f"/flows/{code}").json()

    def list_flows(self, *, category: str | None = None, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category
        return self._request("GET", "/flows", params=params).json()

    def update_flow(self, code: str, yaml_content: str) -> dict[str, Any]:
        return self._request("PATCH", f"/flows/{code}", json={"yaml_content": yaml_content}).json()

    def delete_flow(self, code: str) -> dict[str, Any]:
        return self._request("DELETE", f"/flows/{code}").json()

    # --------------------------------------------------------------------- execution engine

    def start_flow(self, code: str) -> dict[str, Any]:
        return self._request("POST", f"/flows/{code}/start").json()

    def next_step(self, run_id: str, *, decision: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/next", json={"decision": decision}).json()

    def approve_checkpoint(self, run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/approve-checkpoint").json()

    def reject_checkpoint(self, run_id: str, reason: str) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/reject-checkpoint", json={"reason": reason}).json()

    def abort_run(self, run_id: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/abort", json={"reason": reason}).json()

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/runs/{run_id}").json()

    # --------------------------------------------------------------------- stats

    def get_stats(self) -> dict[str, Any]:
        return self._request("GET", "/stats").json()

    # --------------------------------------------------------------------- tokens

    def create_token(
        self,
        name: str,
        scopes: list[str],
        *,
        allowed_categories: list[str] | None = None,
        value: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "scopes": scopes}
        if allowed_categories is not None:
            body["allowed_categories"] = allowed_categories
        if value is not None:
            body["value"] = value
        return self._request("POST", "/tokens", json=body).json()

    def list_tokens(self) -> list[dict[str, Any]]:
        return self._request("GET", "/tokens").json()

    def revoke_token(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/tokens/{name}").json()
