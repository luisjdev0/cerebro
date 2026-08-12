"""Precedencia de variables de entorno (ecosistema-cerebro.md SS4/SS13)."""

from __future__ import annotations

import importlib

from cerebro_clients import config


def _reload():
    importlib.reload(config)


class TestMemoryBaseUrl:
    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_MEMORY_URL", raising=False)
        monkeypatch.delenv("KNOWLEDGEOS_API_URL", raising=False)
        assert config.memory_base_url() == config.DEFAULT_MEMORY_URL

    def test_legacy_knowledgeos_var_is_used_as_fallback(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_MEMORY_URL", raising=False)
        monkeypatch.setenv("KNOWLEDGEOS_API_URL", "http://legacy-host:9999")
        assert config.memory_base_url() == "http://legacy-host:9999"

    def test_cerebro_var_wins_over_legacy(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_MEMORY_URL", "http://new-host:1111")
        monkeypatch.setenv("KNOWLEDGEOS_API_URL", "http://legacy-host:9999")
        assert config.memory_base_url() == "http://new-host:1111"


class TestDocsBaseUrl:
    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_DOCS_URL", raising=False)
        assert config.docs_base_url() == config.DEFAULT_DOCS_URL

    def test_cerebro_var_is_used(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_DOCS_URL", "http://docs-host:2222")
        assert config.docs_base_url() == "http://docs-host:2222"


class TestMemoryToken:
    def test_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        monkeypatch.delenv("KNOWLEDGEOS_API_TOKEN", raising=False)
        assert config.memory_token() == ""

    def test_legacy_knowledgeos_token_is_fallback(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        monkeypatch.setenv("KNOWLEDGEOS_API_TOKEN", "legacy-secret")
        assert config.memory_token() == "legacy-secret"

    def test_cerebro_token_wins_over_legacy(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_TOKEN", "unified-secret")
        monkeypatch.setenv("KNOWLEDGEOS_API_TOKEN", "legacy-secret")
        assert config.memory_token() == "unified-secret"


class TestDocsToken:
    def test_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        assert config.docs_token() == ""

    def test_has_no_legacy_fallback(self, monkeypatch):
        # A diferencia de memory, docs no tiene variable legada que preservar - solo
        # CEREBRO_TOKEN o nada.
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        monkeypatch.setenv("KNOWLEDGEOS_API_TOKEN", "legacy-secret")
        assert config.docs_token() == ""


class TestAgentName:
    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_AGENT_NAME", raising=False)
        monkeypatch.delenv("KNOWLEDGEOS_AGENT_NAME", raising=False)
        assert config.agent_name() == config.DEFAULT_AGENT_NAME

    def test_legacy_fallback(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_AGENT_NAME", raising=False)
        monkeypatch.setenv("KNOWLEDGEOS_AGENT_NAME", "legacy-agent")
        assert config.agent_name() == "legacy-agent"

    def test_cerebro_var_wins(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_AGENT_NAME", "new-agent")
        monkeypatch.setenv("KNOWLEDGEOS_AGENT_NAME", "legacy-agent")
        assert config.agent_name() == "new-agent"
