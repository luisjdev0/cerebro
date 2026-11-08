"""`AuthClient`: 1:1 wrapper over the cerebro-auth HTTP API.

Same criterion as `FlowsClient`/`DocsClient`/`MemoryClient` (see their docstrings):
zero business logic, each method makes one request and returns the decoded JSON or
lets `CerebroAPIError`/`CerebroConnectionError` propagate (see `base.py`).

`login()` is the one exception to "every method sends this client's own bearer
token": it validates a *different* token (passed in the request body), so it must
NOT send the `Authorization` header this client was constructed with -- see
`BaseClient._request(..., authenticated=False)`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from cerebro_clients.base import BaseClient
from cerebro_clients.config import agent_name, auth_base_url, auth_token


class AuthClient(BaseClient):
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
            base_url or auth_base_url(),
            token if token is not None else auth_token(),
            agent or agent_name(),
            timeout=timeout,
            transport=transport,
        )

    # --------------------------------------------------------------------- users

    def create_user(
        self,
        name: str,
        email: str | None = None,
        access_level: str = "user",
        password: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "access_level": access_level}
        if email is not None:
            body["email"] = email
        if password is not None:
            body["password"] = password
        return self._request("POST", "/users", json=body).json()

    def set_password(self, name: str, password: str, current_password: str | None = None) -> None:
        """`POST /users/{name}/password`. An admin may set anyone's; a regular user
        may only change their own and must pass `current_password`. Every session of
        that user is closed by the server. Deliberately has no MCP counterpart: a
        password must not travel through a model's context."""
        body: dict[str, Any] = {"password": password}
        if current_password is not None:
            body["current_password"] = current_password
        self._request("POST", f"/users/{name}/password", json=body)

    def list_users(self) -> list[dict[str, Any]]:
        return self._request("GET", "/users").json()

    # --------------------------------------------------------------------- groups

    def create_group(self, slug: str, name: str) -> dict[str, Any]:
        return self._request("POST", "/groups", json={"slug": slug, "name": name}).json()

    def set_group_scopes(
        self,
        group_slug: str,
        allowed_modules: list[str],
        module_scopes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"allowed_modules": allowed_modules}
        if module_scopes is not None:
            body["module_scopes"] = module_scopes
        return self._request("POST", f"/groups/{group_slug}/scopes", json=body).json()

    def add_group_member(self, group_slug: str, user: str) -> dict[str, Any]:
        return self._request("POST", f"/groups/{group_slug}/members", json={"user": user}).json()

    # --------------------------------------------------------------------- tokens

    def create_token(
        self,
        name: str,
        scopes: list[str],
        allowed_modules: list[str] | None = None,
        module_scopes: dict[str, Any] | None = None,
        user: str | None = None,
        access_level: str | None = None,
        value: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "scopes": scopes}
        if allowed_modules is not None:
            body["allowed_modules"] = allowed_modules
        if module_scopes is not None:
            body["module_scopes"] = module_scopes
        if user is not None:
            body["user"] = user
        if access_level is not None:
            body["access_level"] = access_level
        if value is not None:
            body["value"] = value
        return self._request("POST", "/tokens", json=body).json()

    def list_tokens(self) -> list[dict[str, Any]]:
        return self._request("GET", "/tokens").json()

    def revoke_token(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/tokens/{name}").json()

    # --------------------------------------------------------------------- backup

    def backup(self, dest: Path) -> None:
        """Stream `POST /backup` (a full `pg_dump` of the whole instance) straight to
        `dest` -- see `BaseClient._stream_to_file`. Admin-only on the server side."""
        self._stream_to_file("POST", "/backup", dest)

    # --------------------------------------------------------------------- login

    def login(self, token: str) -> dict[str, Any]:
        # `token` is the credential being validated, sent in the body -- not this
        # client's own Authorization header (`authenticated=False`, see the module
        # docstring). Callers of `login()` typically don't have their own auth token
        # yet (that's the point of logging in), and even when they do, this endpoint
        # checks the *given* token, not the caller's.
        return self._request("POST", "/login", json={"token": token}, authenticated=False).json()

    def login_password(self, name: str, password: str) -> dict[str, Any]:
        """Username + password login (`POST /login/password`). Returns the session
        `token` and its `expires_at`. Like `login()`, sends no Authorization header."""
        return self._request(
            "POST", "/login/password", json={"name": name, "password": password}, authenticated=False
        ).json()

    def logout(self) -> dict[str, Any]:
        """Revoke THIS client's own session token (`POST /logout`)."""
        return self._request("POST", "/logout").json()
