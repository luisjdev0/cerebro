"""Utilities shared by test_memory_client.py and test_docs_client.py: a test
`httpx.BaseTransport` that records every request (method, path, params, json) instead
of touching the network -- this way we verify "routes to the correct API" without a live API
(ecosistema-cerebro.md SS15)."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, response_json: Any = None, status_code: int = 200):
        self.calls: list[dict[str, Any]] = []
        self.response_json = response_json if response_json is not None else {}
        self.status_code = status_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            body = jsonlib.loads(request.content)
        self.calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "json": body,
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(self.status_code, json=self.response_json, request=request)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


class RaisingTransport(httpx.BaseTransport):
    """Simulates a total network failure (DNS, connection refused...)."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)
