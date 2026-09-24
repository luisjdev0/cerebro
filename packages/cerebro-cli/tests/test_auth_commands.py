"""`cerebro login`/`cerebro user <subcommand>`/`cerebro group <subcommand>` must route
to the correct `AuthClient` calls and not leak business logic of their own -- same
mocking pattern as `test_memory_commands.py`/`test_docs_commands.py` (a `MagicMock`
injected via the `client=` kwarg, no real network)."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError, CerebroConnectionError

from cerebro_cli import auth_commands, tokens


def _api_error(status_code, detail="boom"):
    return CerebroAPIError(status_code, detail, response=MagicMock())


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "STATE_DIR", tmp_path / ".cerebro")
    monkeypatch.setattr(tokens, "CONFIG_PATH", tmp_path / ".cerebro" / "config.json")
    return tmp_path / ".cerebro"


# --------------------------------------------------------------------------- login


class TestLogin:
    def test_success_persists_config_and_prints_summary(self, capsys, isolated_state_dir):
        client = MagicMock()
        client.login.return_value = {"name": "jose", "access_level": "admin", "allowed_modules": ["memory", "docs"]}
        args = argparse.Namespace(token="cbr_abc123", url="http://localhost:8030")

        auth_commands.cmd_login(args, client=client)

        client.login.assert_called_once_with("cbr_abc123")
        out = capsys.readouterr().out
        assert "jose" in out and "admin" in out

        saved = json.loads((isolated_state_dir / "config.json").read_text(encoding="utf-8"))
        assert saved["token"] == "cbr_abc123"
        assert saved["url"] == "http://localhost:8030"

    def test_401_does_not_write_config_and_exits(self, isolated_state_dir):
        client = MagicMock()
        client.login.side_effect = _api_error(401, "invalid token")
        args = argparse.Namespace(token="bad-token", url=None)

        with pytest.raises(SystemExit) as exc_info:
            auth_commands.cmd_login(args, client=client)
        assert exc_info.value.code != 0
        assert not (isolated_state_dir / "config.json").exists()

    def test_connection_error_does_not_write_config_and_exits(self, isolated_state_dir):
        client = MagicMock()
        client.login.side_effect = CerebroConnectionError("http://x", RuntimeError("refused"))
        args = argparse.Namespace(token="cbr_abc123", url=None)

        with pytest.raises(SystemExit):
            auth_commands.cmd_login(args, client=client)
        assert not (isolated_state_dir / "config.json").exists()


# --------------------------------------------------------------------------- user


class TestUserCreate:
    def test_routes_to_client(self, capsys):
        client = MagicMock()
        client.create_user.return_value = {"id": "u1", "name": "jose", "access_level": "user"}
        args = argparse.Namespace(name="jose", email="jose@example.com", access_level="user")

        auth_commands.cmd_user_create(args, client=client)

        client.create_user.assert_called_once_with("jose", email="jose@example.com", access_level="user")
        out = capsys.readouterr().out
        assert "jose" in out and "u1" in out

    def test_api_error_exits(self):
        client = MagicMock()
        client.create_user.side_effect = _api_error(409, "name taken")
        args = argparse.Namespace(name="jose", email=None, access_level="user")
        with pytest.raises(SystemExit):
            auth_commands.cmd_user_create(args, client=client)


class TestUserList:
    def test_prints_rows(self, capsys):
        client = MagicMock()
        client.list_users.return_value = [
            {"id": "u1", "name": "jose", "email": "jose@example.com", "access_level": "admin"}
        ]
        auth_commands.cmd_user_list(argparse.Namespace(), client=client)
        client.list_users.assert_called_once_with()
        assert "jose" in capsys.readouterr().out

    def test_empty_list_prints_placeholder(self, capsys):
        client = MagicMock()
        client.list_users.return_value = []
        auth_commands.cmd_user_list(argparse.Namespace(), client=client)
        assert "sin usuarios" in capsys.readouterr().out


# --------------------------------------------------------------------------- group


class TestGroupCreate:
    def test_defaults_name_to_slug(self, capsys):
        client = MagicMock()
        client.create_group.return_value = {"slug": "eco"}
        args = argparse.Namespace(slug="eco", name=None)
        auth_commands.cmd_group_create(args, client=client)
        client.create_group.assert_called_once_with("eco", "eco")

    def test_uses_given_name(self):
        client = MagicMock()
        client.create_group.return_value = {"slug": "eco"}
        args = argparse.Namespace(slug="eco", name="Ecosistema")
        auth_commands.cmd_group_create(args, client=client)
        client.create_group.assert_called_once_with("eco", "Ecosistema")


class TestGroupSetScopes:
    def test_packs_module_scopes_only_for_flags_given(self):
        client = MagicMock()
        client.set_group_scopes.return_value = {"slug": "eco"}
        args = argparse.Namespace(
            slug="eco",
            modules="memory,docs",
            memory_contexts="ctx-a,ctx-b",
            docs_categories=None,
            flows_categories="flow-a",
        )
        auth_commands.cmd_group_set_scopes(args, client=client)
        client.set_group_scopes.assert_called_once_with(
            "eco",
            ["memory", "docs"],
            module_scopes={"memory": {"contexts": ["ctx-a", "ctx-b"]}, "flows": {"categories": ["flow-a"]}},
        )

    def test_no_scope_flags_passes_none(self):
        client = MagicMock()
        client.set_group_scopes.return_value = {"slug": "eco"}
        args = argparse.Namespace(
            slug="eco", modules="memory", memory_contexts=None, docs_categories=None, flows_categories=None
        )
        auth_commands.cmd_group_set_scopes(args, client=client)
        client.set_group_scopes.assert_called_once_with("eco", ["memory"], module_scopes=None)


class TestGroupAddMember:
    def test_routes_to_client(self, capsys):
        client = MagicMock()
        args = argparse.Namespace(slug="eco", user="jose")
        auth_commands.cmd_group_add_member(args, client=client)
        client.add_group_member.assert_called_once_with("eco", "jose")
        assert "jose" in capsys.readouterr().out

    def test_api_error_exits(self):
        client = MagicMock()
        client.add_group_member.side_effect = _api_error(404, "no such group")
        args = argparse.Namespace(slug="eco", user="jose")
        with pytest.raises(SystemExit):
            auth_commands.cmd_group_add_member(args, client=client)
