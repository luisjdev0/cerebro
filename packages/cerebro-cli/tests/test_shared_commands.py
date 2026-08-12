"""`cerebro backup`/`restore` (mock de subprocess.run, sin docker real) y `cerebro
token create/revoke` transversal (mock de MemoryClient/DocsClient) -- en particular la
semantica de fallo parcial e idempotencia por reintento (ecosistema-cerebro.md SS13).
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError, CerebroConnectionError

from cerebro_cli import shared_commands, tokens


@pytest.fixture(autouse=True)
def isolated_pending_dir(tmp_path, monkeypatch):
    pending_dir = tmp_path / "pending-tokens"
    monkeypatch.setattr(tokens, "PENDING_TOKENS_DIR", pending_dir)
    return pending_dir


def _api_error(status_code, detail="boom"):
    return CerebroAPIError(status_code, detail, response=MagicMock())


# --------------------------------------------------------------------------- backup / restore


class TestBackup:
    def test_runs_pg_dump_and_reports_success(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(cmd, cwd, stdout, stderr):
            captured["cmd"] = cmd
            stdout.write(b"-- dump content --")
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(shared_commands.subprocess, "run", fake_run)
        args = argparse.Namespace(output=str(tmp_path))
        shared_commands.cmd_backup(args)

        assert captured["cmd"][:4] == ["docker", "compose", "exec", "-T"]
        assert "pg_dump" in captured["cmd"]
        files = list(tmp_path.glob("cerebro-*.sql"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"-- dump content --"

    def test_nonzero_exit_removes_partial_file_and_exits(self, monkeypatch, tmp_path):
        def fake_run(cmd, cwd, stdout, stderr):
            stdout.write(b"partial")
            result = MagicMock()
            result.returncode = 1
            result.stderr = b"pg_dump failed"
            return result

        monkeypatch.setattr(shared_commands.subprocess, "run", fake_run)
        args = argparse.Namespace(output=str(tmp_path))
        with pytest.raises(SystemExit):
            shared_commands.cmd_backup(args)
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


# --------------------------------------------------------------------------- token transversal


class TestTokenCreateTransversal:
    def test_full_success_prints_token_and_clears_pending_state(self, capsys):
        memory_client = MagicMock()
        docs_client = MagicMock()
        args = argparse.Namespace(name="agente-x", scopes="read,write", contexts=None, categories=None)

        shared_commands.cmd_token_create(args, memory_client=memory_client, docs_client=docs_client)

        # el MISMO secreto se paso a ambos servicios
        memory_value = memory_client.create_token.call_args.kwargs["value"]
        docs_value = docs_client.create_token.call_args.kwargs["value"]
        assert memory_value == docs_value
        assert memory_value.startswith(tokens.TOKEN_PREFIX)

        out = capsys.readouterr().out
        assert memory_value in out
        assert tokens.load_pending_value("agente-x") is None  # limpiado tras exito total

    def test_partial_failure_exits_nonzero_and_keeps_pending_state(self, capsys):
        memory_client = MagicMock()
        docs_client = MagicMock()
        docs_client.create_token.side_effect = _api_error(500, "internal error")
        args = argparse.Namespace(name="agente-y", scopes="read", contexts=None, categories=None)

        with pytest.raises(SystemExit) as exc_info:
            shared_commands.cmd_token_create(args, memory_client=memory_client, docs_client=docs_client)
        assert exc_info.value.code != 0

        out = capsys.readouterr().out
        assert "cerebro-memory: ok" in out
        assert "cerebro-docs" in out and "error" in out
        assert tokens.load_pending_value("agente-y") is not None  # queda pendiente para reintentar

    def test_retry_after_partial_failure_reuses_same_secret_and_completes(self, capsys):
        # Primer intento: memory ok, docs falla.
        memory_client_1 = MagicMock()
        docs_client_1 = MagicMock()
        docs_client_1.create_token.side_effect = CerebroConnectionError("http://docs", RuntimeError("refused"))
        args = argparse.Namespace(name="agente-z", scopes="read", contexts=None, categories=None)
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args, memory_client=memory_client_1, docs_client=docs_client_1)
        first_value = memory_client_1.create_token.call_args.kwargs["value"]

        # Reintento (mismo comando): debe reusar el MISMO secreto, no generar uno nuevo.
        memory_client_2 = MagicMock()
        docs_client_2 = MagicMock()
        shared_commands.cmd_token_create(args, memory_client=memory_client_2, docs_client=docs_client_2)

        assert memory_client_2.create_token.call_args.kwargs["value"] == first_value
        assert docs_client_2.create_token.call_args.kwargs["value"] == first_value
        assert tokens.load_pending_value("agente-z") is None

    def test_scopes_and_allowed_lists_are_parsed_from_csv(self):
        memory_client = MagicMock()
        docs_client = MagicMock()
        args = argparse.Namespace(
            name="agente-w", scopes="read, write", contexts="ctx-a,ctx-b", categories="cat-a"
        )
        shared_commands.cmd_token_create(args, memory_client=memory_client, docs_client=docs_client)

        assert memory_client.create_token.call_args.args[1] == ["read", "write"]
        assert memory_client.create_token.call_args.kwargs["allowed_contexts"] == ["ctx-a", "ctx-b"]
        assert docs_client.create_token.call_args.kwargs["allowed_categories"] == ["cat-a"]


class TestTokenRevokeTransversal:
    def test_full_success(self, capsys):
        memory_client = MagicMock()
        docs_client = MagicMock()
        args = argparse.Namespace(name="agente-x")
        shared_commands.cmd_token_revoke(args, memory_client=memory_client, docs_client=docs_client)
        memory_client.revoke_token.assert_called_once_with("agente-x")
        docs_client.revoke_token.assert_called_once_with("agente-x")

    def test_404_on_one_service_counts_as_already_done_not_a_failure(self, capsys):
        memory_client = MagicMock()
        docs_client = MagicMock()
        docs_client.revoke_token.side_effect = _api_error(404, "no active token")
        args = argparse.Namespace(name="agente-x")
        # no debe lanzar SystemExit - un 404 es "ya estaba revocado", exito equivalente.
        shared_commands.cmd_token_revoke(args, memory_client=memory_client, docs_client=docs_client)

    def test_real_failure_on_one_service_exits_nonzero(self):
        memory_client = MagicMock()
        docs_client = MagicMock()
        docs_client.revoke_token.side_effect = _api_error(500, "internal error")
        args = argparse.Namespace(name="agente-x")
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_revoke(args, memory_client=memory_client, docs_client=docs_client)
