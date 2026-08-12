"""Transporte HTTP compartido entre `MemoryClient` y `DocsClient`.

Ambos son wrappers DELGADOS 1:1 sobre los endpoints HTTP de sus respectivas APIs
(ecosistema-cerebro.md SS4/SS14): sin validaciones propias, sin reintentos
inteligentes, sin traduccion de errores a mensajes "amigables" para un LLM -- eso vive
en las capas de presentacion (`cerebro-mcp`, `cerebro-cli`), no aqui. Lo unico que
este modulo hace con un error es propagarlo con un mensaje claro (status code + el
`detail` del servidor si vino en JSON) en vez de dejar pasar un `httpx.HTTPStatusError`
crudo o, peor, tragarselo en silencio.
"""

from __future__ import annotations

from typing import Any

import httpx


class CerebroAPIError(RuntimeError):
    """Una respuesta HTTP >= 400 de cerebro-memory o cerebro-docs.

    `status_code` y `detail` quedan accesibles sin que el caller tenga que volver a
    parsear `response` -- `detail` es lo que el servidor mando en `{"detail": ...}`
    si la respuesta era JSON, o el texto crudo si no.
    """

    def __init__(self, status_code: int, detail: Any, *, response: httpx.Response):
        self.status_code = status_code
        self.detail = detail
        self.response = response
        super().__init__(f"HTTP {status_code}: {detail}")


class CerebroConnectionError(RuntimeError):
    """No se pudo alcanzar la API en absoluto (DNS, conexion rechazada, timeout...)."""

    def __init__(self, base_url: str, exc: httpx.RequestError):
        self.base_url = base_url
        self.original = exc
        super().__init__(f"no se pudo conectar con {base_url}: {exc}")


class BaseClient:
    """`httpx.Client` sincrono con auth Bearer + `X-Agent-Name`, y traduccion minima
    de errores de red/HTTP a las dos excepciones de arriba -- nunca las traga."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        agent_name: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        # `transport` no es config real de produccion: es un gancho para pruebas
        # (httpx.MockTransport, ver tests/) que reemplaza el transporte de red real
        # sin necesitar una API viva -- ecosistema-cerebro.md SS15 ("mocks/transport
        # de httpx, sin necesidad de APIs vivas").
        self.base_url = base_url.rstrip("/")
        headers = {"X-Agent-Name": agent_name}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BaseClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise CerebroConnectionError(self.base_url, exc) from exc

        if resp.status_code >= 400:
            detail: Any
            try:
                body = resp.json()
                detail = body.get("detail", resp.text) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            raise CerebroAPIError(resp.status_code, detail, response=resp) from None

        return resp
