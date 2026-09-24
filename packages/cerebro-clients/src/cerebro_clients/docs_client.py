"""`DocsClient`: 1:1 wrapper over the cerebro-docs HTTP API.

Same criterion as `MemoryClient` (see its docstring): zero business logic, each
method makes one request and returns the decoded JSON or lets
`CerebroAPIError`/`CerebroConnectionError` propagate. `docs_search`/`docs_list` (cerebro-mcp) and
`docs search`/`docs list` (cerebro-cli) are two UX surfaces over the SAME
`GET /documents` endpoint -- that's why there's only one `list_documents` here, with an
optional `q`, instead of two separate methods.
"""

from __future__ import annotations

from typing import Any

import httpx

from cerebro_clients.base import BaseClient
from cerebro_clients.config import agent_name, docs_base_url, docs_token


class DocsClient(BaseClient):
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
            base_url or docs_base_url(),
            token if token is not None else docs_token(),
            agent or agent_name(),
            timeout=timeout,
            transport=transport,
        )

    # --------------------------------------------------------------------- health

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").json()

    # --------------------------------------------------------------------- categories

    def create_category(
        self,
        slug: str,
        name: str,
        *,
        description: str | None = None,
        hidden: bool = False,
        locked: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"slug": slug, "name": name, "hidden": hidden, "locked": locked}
        if description is not None:
            body["description"] = description
        return self._request("POST", "/categories", json=body).json()

    def list_categories(self) -> list[dict[str, Any]]:
        return self._request("GET", "/categories").json()

    def update_category(
        self,
        slug: str,
        *,
        new_slug: str | None = None,
        name: str | None = None,
        description: str | None = None,
        hidden: bool | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if new_slug is not None:
            body["slug"] = new_slug
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if hidden is not None:
            body["hidden"] = hidden
        return self._request("PATCH", f"/categories/{slug}", json=body).json()

    def delete_category(self, slug: str, *, force: bool = False) -> dict[str, Any]:
        return self._request("DELETE", f"/categories/{slug}", params={"force": force}).json()

    # --------------------------------------------------------------------- documents

    def create_document(self, title: str, content: str, category: str, *, slug: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "content": content, "category": category}
        if slug is not None:
            body["slug"] = slug
        return self._request("POST", "/documents", json=body).json()

    def get_document(self, category: str, slug: str) -> dict[str, Any]:
        return self._request("GET", f"/documents/{category}/{slug}").json()

    def list_documents(
        self,
        *,
        category: str | None = None,
        q: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category
        if q is not None:
            params["q"] = q
        return self._request("GET", "/documents", params=params).json()

    def update_document(
        self, document_id: str, title: str, content: str, category: str, *, slug: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "content": content, "category": category}
        if slug is not None:
            body["slug"] = slug
        return self._request("PATCH", f"/documents/{document_id}", json=body).json()

    def patch_section(
        self,
        document_id: str,
        heading: str,
        operation: str,
        *,
        body: str = "",
        create_if_missing: bool = False,
        new_heading_level: int = 2,
    ) -> dict[str, Any]:
        payload = {
            "heading": heading,
            "operation": operation,
            "body": body,
            "create_if_missing": create_if_missing,
            "new_heading_level": new_heading_level,
        }
        return self._request("PATCH", f"/documents/{document_id}/section", json=payload).json()

    def delete_document(self, document_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/documents/{document_id}").json()

    def list_archived_documents(
        self, *, category: str | None = None, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category
        return self._request("GET", "/documents/archived", params=params).json()

    def archive_document(self, document_id: str) -> dict[str, Any]:
        return self._request("POST", f"/documents/{document_id}/archive").json()

    def unarchive_document(self, document_id: str) -> dict[str, Any]:
        return self._request("POST", f"/documents/{document_id}/unarchive").json()

    def get_document_versions(self, document_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/documents/{document_id}/versions").json()

    # --------------------------------------------------------------------- stats

    def get_stats(self) -> dict[str, Any]:
        return self._request("GET", "/stats").json()
