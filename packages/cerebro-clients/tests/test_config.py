"""Environment variable precedence (ecosistema-cerebro.md SS4/SS13)."""

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
        # Unlike memory, docs has no legacy variable to preserve - only
        # CEREBRO_TOKEN or nothing.
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        monkeypatch.setenv("KNOWLEDGEOS_API_TOKEN", "legacy-secret")
        assert config.docs_token() == ""


class TestAuthBaseUrl:
    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_AUTH_URL", raising=False)
        assert config.auth_base_url() == config.DEFAULT_AUTH_URL

    def test_cerebro_var_is_used(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_AUTH_URL", "http://auth-host:3333")
        assert config.auth_base_url() == "http://auth-host:3333"


class TestAuthToken:
    def test_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        assert config.auth_token() == ""

    def test_has_no_legacy_fallback(self, monkeypatch):
        # Like docs (unlike memory), auth is a new service with no legacy variable
        # to preserve - only CEREBRO_TOKEN or nothing.
        monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
        monkeypatch.setenv("KNOWLEDGEOS_API_TOKEN", "legacy-secret")
        assert config.auth_token() == ""

    def test_cerebro_token_is_used(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_TOKEN", "unified-secret")
        assert config.auth_token() == "unified-secret"


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


class TestSavedLoginUrl:
    """`cerebro login` saves the GATEWAY url; each module's url is derived under /api/<module>."""

    def test_modules_are_derived_under_the_api_prefix(self, monkeypatch, tmp_path):
        saved = tmp_path / "config.json"
        saved.write_text('{"url": "https://cerebro.example.com/", "token": "t"}', encoding="utf-8")
        monkeypatch.setattr(config, "_CONFIG_PATH", saved)
        for var in ("CEREBRO_MEMORY_URL", "KNOWLEDGEOS_API_URL", "CEREBRO_DOCS_URL", "CEREBRO_FLOWS_URL", "CEREBRO_AUTH_URL"):
            monkeypatch.delenv(var, raising=False)
        assert config.memory_base_url() == "https://cerebro.example.com/api/memory"
        assert config.docs_base_url() == "https://cerebro.example.com/api/docs"
        assert config.flows_base_url() == "https://cerebro.example.com/api/flows"
        assert config.auth_base_url() == "https://cerebro.example.com/api/auth"

    def test_an_explicit_variable_still_wins_and_may_use_the_legacy_prefix(self, monkeypatch, tmp_path):
        saved = tmp_path / "config.json"
        saved.write_text('{"url": "https://cerebro.example.com"}', encoding="utf-8")
        monkeypatch.setattr(config, "_CONFIG_PATH", saved)
        monkeypatch.setenv("CEREBRO_MEMORY_URL", "https://cerebro.example.com/memory")
        assert config.memory_base_url() == "https://cerebro.example.com/memory"
