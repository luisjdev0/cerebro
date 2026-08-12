"""Utilidades compartidas por test_memory_client.py y test_docs_client.py: un
`httpx.BaseTransport` de prueba que graba cada request (metodo, path, params, json) en
vez de tocar la red -- asi verificamos "enruta a la API correcta" sin una API viva
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
    """Simula un fallo de red total (DNS, conexion rechazada...)."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)
