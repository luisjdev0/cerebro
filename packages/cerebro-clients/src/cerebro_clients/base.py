"""HTTP transport shared between `MemoryClient` and `DocsClient`.

Both are THIN 1:1 wrappers over the HTTP endpoints of their respective APIs
(ecosistema-cerebro.md SS4/SS14): no validation of their own, no smart
retries, no translation of errors into "friendly" messages for an LLM -- that lives
in the presentation layers (`cerebro-mcp`, `cerebro-cli`), not here. The only thing
this module does with an error is propagate it with a clear message (status code + the
server's `detail` if it came as JSON) instead of letting a raw `httpx.HTTPStatusError`
pass through or, worse, swallowing it silently.
"""

from __future__ import annotations

from typing import Any

import httpx


class CerebroAPIError(RuntimeError):
    """An HTTP response >= 400 from cerebro-memory, cerebro-docs, cerebro-flows, or cerebro-auth.

    `status_code` and `detail` are accessible without the caller having to re-parse
    `response` -- `detail` is what the server sent in `{"detail": ...}`
    if the response was JSON, or the raw text if not.
    """

    def __init__(self, status_code: int, detail: Any, *, response: httpx.Response):
        self.status_code = status_code
        self.detail = detail
        self.response = response
        super().__init__(f"HTTP {status_code}: {detail}")


class CerebroConnectionError(RuntimeError):
    """Could not reach the API at all (DNS, connection refused, timeout...)."""

    def __init__(self, base_url: str, exc: httpx.RequestError):
        self.base_url = base_url
        self.original = exc
        super().__init__(f"no se pudo conectar con {base_url}: {exc}")


class BaseClient:
    """Synchronous `httpx.Client` with Bearer auth + `X-Agent-Name`, and minimal
    translation of network/HTTP errors into the two exceptions above -- never swallows them."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        agent_name: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        # `transport` is not real production config: it's a hook for tests
        # (httpx.MockTransport, see tests/) that replaces the real network transport
        # without needing a live API -- ecosistema-cerebro.md SS15 ("httpx mocks/transport,
        # no live APIs needed").
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

    def _request(self, method: str, path: str, *, authenticated: bool = True, **kwargs: Any) -> httpx.Response:
        # `authenticated=False` is for endpoints like cerebro-auth's `POST /login`,
        # which validate a *different* token (given in the request body) and must not
        # send this client's own bearer token -- so we build the request the same way
        # and then strip the `Authorization` header the constructor put in
        # `self._client.headers` before sending it, instead of skipping auth entirely
        # via a separate unauthenticated client/transport.
        try:
            if authenticated:
                resp = self._client.request(method, path, **kwargs)
            else:
                request = self._client.build_request(method, path, **kwargs)
                request.headers.pop("Authorization", None)
                resp = self._client.send(request)
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
