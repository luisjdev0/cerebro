"""Unitarios del hook de resolver de ambiguedad (Fase 4, plan_v2.md SS8).

`NullResolver` y `OllamaResolver` no tocan Postgres - Ollama se mockea via
`httpx.AsyncClient`, asi que esto corre sin ninguna infraestructura externa (ni
Postgres ni un Ollama real). Ver tests/test_context_engine.py para el resto del
Context Engine (Fase 2).
"""

from __future__ import annotations

import httpx
import pytest

from cerebro_memory import context_engine
from cerebro_memory.config import Settings
from cerebro_memory.context_engine import (
    ContextCandidate,
    NullResolver,
    OllamaResolver,
    build_ambiguity_resolver,
)

CANDIDATES = [
    ContextCandidate(slug="finanzas-personales", name="Finanzas Personales", description="Gastos e ingresos personales", score=0.55),
    ContextCandidate(slug="expense-tracker", name="Expense Tracker", description="Proyecto de software de gastos", score=0.45),
]


# --------------------------------------------------------------------------- NullResolver


async def test_null_resolver_always_returns_none():
    resolver = NullResolver()
    assert await resolver.resolve("cuanto gaste este mes", CANDIDATES) is None


# --------------------------------------------------------------------------- fakes for Ollama


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200) -> None:
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._json


def _fake_async_client(post_impl):
    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, *args, **kwargs):
            return await post_impl(*args, **kwargs)

    return _FakeAsyncClient


# --------------------------------------------------------------------------- OllamaResolver


async def test_ollama_resolver_valid_response_returns_matching_slug(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        return _FakeResponse({"response": "finanzas-personales"})

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("cuanto gaste este mes", CANDIDATES)
    assert result == "finanzas-personales"


async def test_ollama_resolver_response_with_extra_text_still_matches_slug(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        return _FakeResponse({"response": "El contexto es expense-tracker.\n"})

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("gasto del expense tracker", CANDIDATES)
    assert result == "expense-tracker"


async def test_ollama_resolver_explicit_none_answer_returns_none(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        return _FakeResponse({"response": "none"})

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("algo ambiguo", CANDIDATES)
    assert result is None


async def test_ollama_resolver_unparseable_slug_returns_none(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        return _FakeResponse({"response": "no tengo idea, podria ser cualquier cosa"})

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("algo ambiguo", CANDIDATES)
    assert result is None


async def test_ollama_resolver_timeout_falls_back_to_none(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b", timeout=2.0)
    result = await resolver.resolve("cuanto gaste este mes", CANDIDATES)
    assert result is None


async def test_ollama_resolver_connection_error_falls_back_to_none(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("cuanto gaste este mes", CANDIDATES)
    assert result is None


async def test_ollama_resolver_http_error_status_falls_back_to_none(monkeypatch: pytest.MonkeyPatch):
    async def post_impl(*args, **kwargs):
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("cuanto gaste este mes", CANDIDATES)
    assert result is None


async def test_ollama_resolver_no_candidates_returns_none_without_calling_ollama(monkeypatch: pytest.MonkeyPatch):
    called = False

    async def post_impl(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeResponse({"response": "none"})

    monkeypatch.setattr(context_engine.httpx, "AsyncClient", _fake_async_client(post_impl))

    resolver = OllamaResolver(url="http://localhost:11434", model="qwen2.5:1.5b")
    result = await resolver.resolve("algo", [])
    assert result is None
    assert called is False


# --------------------------------------------------------------------------- factory


def test_build_ambiguity_resolver_defaults_to_null_resolver():
    settings = Settings(_env_file=None)
    resolver = build_ambiguity_resolver(settings)
    assert isinstance(resolver, NullResolver)


def test_build_ambiguity_resolver_ollama_when_configured():
    settings = Settings(_env_file=None, context_engine_resolver="ollama", ollama_url="http://x:1", ollama_model="m")
    resolver = build_ambiguity_resolver(settings)
    assert isinstance(resolver, OllamaResolver)
