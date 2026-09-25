"""`cerebro backup` (mocked `AuthClient`, no real cerebro-auth), `cerebro restore`
(mocked subprocess.run, no real docker) and `cerebro token create/revoke` (mocked
`AuthClient`) -- token management now lives in exactly one service (cerebro-auth,
ecosistema-cerebro.md SS13, updated), so there's no more partial-failure/retry
semantics to test across services -- just the client-side validation
(`--user`/`--access-level` mutual exclusion, `--modules` required for a service
token) and that the CSV flags get packed the way the server expects.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError, CerebroConnectionError

from cerebro_cli import shared_commands


def _api_error(status_code, detail="boom"):
    return CerebroAPIError(status_code, detail, response=MagicMock())


def _token_create_args(**overrides):
    defaults = dict(
        name="agente-x",
        scopes="read,write",
        modules="memory,docs",
        user=None,
        access_level="user",
        memory_contexts=None,
        docs_categories=None,
        flows_categories=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --------------------------------------------------------------------------- backup / restore


class TestBackup:
    def test_downloads_via_auth_client_and_reports_success(self, tmp_path):
        client = MagicMock()

        def fake_backup(dest):
            dest.write_bytes(b"-- dump content --")

        client.backup.side_effect = fake_backup
        args = argparse.Namespace(output=str(tmp_path))

        shared_commands.cmd_backup(args, client=client)

        client.backup.assert_called_once()
        (dest_arg,) = client.backup.call_args.args
        assert dest_arg.parent == tmp_path
        files = list(tmp_path.glob("cerebro-*.sql"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"-- dump content --"

    def test_connection_error_exits_and_leaves_no_partial_file(self, tmp_path):
        client = MagicMock()
        client.backup.side_effect = CerebroConnectionError("http://x", RuntimeError("refused"))
        args = argparse.Namespace(output=str(tmp_path))

        with pytest.raises(SystemExit):
            shared_commands.cmd_backup(args, client=client)
        assert list(tmp_path.glob("cerebro-*.sql")) == []

    def test_api_error_exits_and_leaves_no_partial_file(self, tmp_path):
        client = MagicMock()
        client.backup.side_effect = _api_error(403, "admin access required")
        args = argparse.Namespace(output=str(tmp_path))

        with pytest.raises(SystemExit):
            shared_commands.cmd_backup(args, client=client)
        assert list(tmp_path.glob("cerebro-*.sql")) == []


class TestRestore:
    def test_skips_when_file_missing(self, tmp_path, capsys):
        args = argparse.Namespace(file=str(tmp_path / "does-not-exist.sql"), yes=True)
        with pytest.raises(SystemExit):
            shared_commands.cmd_restore(args)

    def test_runs_psql_when_confirmed(self, monkeypatch, tmp_path):
        dump = tmp_path / "backup.sql"
        dump.write_text("-- dump --")
        captured = {}

        def fake_run(cmd, cwd, stdin, stderr):
            captured["cmd"] = cmd
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(shared_commands.subprocess, "run", fake_run)
        args = argparse.Namespace(file=str(dump), yes=True)
        shared_commands.cmd_restore(args)
        assert "psql" in captured["cmd"]

    def test_declines_without_yes_flag_when_user_says_no(self, monkeypatch, tmp_path):
        dump = tmp_path / "backup.sql"
        dump.write_text("-- dump --")
        monkeypatch.setattr("builtins.input", lambda _: "no")
        run_mock = MagicMock()
        monkeypatch.setattr(shared_commands.subprocess, "run", run_mock)
        args = argparse.Namespace(file=str(dump), yes=False)
        shared_commands.cmd_restore(args)
        run_mock.assert_not_called()


# --------------------------------------------------------------------------- token create


class TestTokenCreate:
    def test_full_success_prints_token(self, capsys):
        client = MagicMock()
        client.create_token.return_value = {"name": "agente-x", "scopes": ["read", "write"], "token": "sometoken123"}
        args = _token_create_args()

        shared_commands.cmd_token_create(args, client=client)

        client.create_token.assert_called_once_with(
            "agente-x",
            ["read", "write"],
            allowed_modules=["memory", "docs"],
            module_scopes=None,
            user=None,
            access_level="user",
        )
        out = capsys.readouterr().out
        assert "sometoken123" in out

    def test_rejects_user_and_access_level_together(self):
        args = _token_create_args(user="jose", access_level="owner")
        with pytest.raises(SystemExit) as exc_info:
            shared_commands.cmd_token_create(args, client=MagicMock())
        assert exc_info.value.code != 0

    def test_requires_user_or_access_level(self):
        args = _token_create_args(user=None, access_level=None)
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args, client=MagicMock())

    def test_service_token_without_user_requires_modules(self):
        args = _token_create_args(user=None, access_level="admin", modules=None)
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args, client=MagicMock())

    def test_user_owned_token_can_omit_modules(self):
        client = MagicMock()
        client.create_token.return_value = {"name": "agente-x", "scopes": ["read"], "token": "tok"}
        args = _token_create_args(user="jose", access_level=None, modules=None)

        shared_commands.cmd_token_create(args, client=client)

        client.create_token.assert_called_once_with(
            "agente-x",
            ["read", "write"],
            allowed_modules=None,
            module_scopes=None,
            user="jose",
            access_level=None,
        )

    def test_packs_module_scopes_only_for_flags_actually_given(self):
        client = MagicMock()
        client.create_token.return_value = {"name": "agente-x", "scopes": ["read"], "token": "tok"}
        args = _token_create_args(memory_contexts="ctx-a, ctx-b", docs_categories="cat-a")

        shared_commands.cmd_token_create(args, client=client)

        module_scopes = client.create_token.call_args.kwargs["module_scopes"]
        assert module_scopes == {"memory": {"contexts": ["ctx-a", "ctx-b"]}, "docs": {"categories": ["cat-a"]}}
        assert "flows" not in module_scopes

    def test_connection_error_exits(self):
        client = MagicMock()
        client.create_token.side_effect = CerebroConnectionError("http://x", RuntimeError("refused"))
        args = _token_create_args()
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args, client=client)

    def test_api_error_exits(self):
        client = MagicMock()
        client.create_token.side_effect = _api_error(400, "invalid scopes")
        args = _token_create_args()
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args, client=client)


# --------------------------------------------------------------------------- token revoke


class TestTokenRevoke:
    def test_success(self, capsys):
        client = MagicMock()
        args = argparse.Namespace(name="agente-x")
        shared_commands.cmd_token_revoke(args, client=client)
        client.revoke_token.assert_called_once_with("agente-x")
        assert "revocado" in capsys.readouterr().out

    def test_404_counts_as_already_done_not_a_failure(self, capsys):
        client = MagicMock()
        client.revoke_token.side_effect = _api_error(404, "no active token")
        args = argparse.Namespace(name="agente-x")
        # must not raise SystemExit - a 404 means "was already revoked", equivalent to success.
        shared_commands.cmd_token_revoke(args, client=client)
        assert "ya no estaba activo" in capsys.readouterr().out

    def test_real_failure_exits_nonzero(self):
        client = MagicMock()
        client.revoke_token.side_effect = _api_error(500, "internal error")
        args = argparse.Namespace(name="agente-x")
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_revoke(args, client=client)

    def test_connection_error_exits(self):
        client = MagicMock()
        client.revoke_token.side_effect = CerebroConnectionError("http://x", RuntimeError("refused"))
        args = argparse.Namespace(name="agente-x")
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_revoke(args, client=client)
