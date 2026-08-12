"""`cerebro memory <subcomando>` debe enrutar al `MemoryClient` correcto y no filtrar
logica de negocio propia (salvo la orquestacion ya existente del importador de
Markdown, que se prueba explicitamente aparte)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError

from cerebro_cli import memory_commands


def _api_error(status_code, detail="boom"):
    return CerebroAPIError(status_code, detail, response=MagicMock())


def test_stats_prints_from_client(capsys):
    client = MagicMock()
    client.get_stats.return_value = {
        "memories_by_context": [{"context": "ctx", "status": "active", "count": 3}],
        "disambiguations": {"total": 1, "auto": 1, "agent": 0, "user": 0, "local_model": 0, "unresolved": 0},
        "preferences_learned": [],
    }
    memory_commands.cmd_stats(argparse.Namespace(), client=client)
    out = capsys.readouterr().out
    assert "ctx" in out
    client.get_stats.assert_called_once_with()


def test_stats_connection_error_exits(monkeypatch):
    client = MagicMock()
    from cerebro_clients import CerebroConnectionError

    client.get_stats.side_effect = CerebroConnectionError("http://x", RuntimeError("refused"))
    with pytest.raises(SystemExit):
        memory_commands.cmd_stats(argparse.Namespace(), client=client)


def test_export_disambiguations_writes_jsonl(tmp_path):
    client = MagicMock()
    client.export_disambiguations.return_value = [
        {"query": "q1", "candidates": [], "chosen_context": "ctx", "resolved_by": "auto", "created_at": "2026-01-01"}
    ]
    out_file = tmp_path / "out.jsonl"
    args = argparse.Namespace(resolved_only=False, output=str(out_file))
    memory_commands.cmd_export_disambiguations(args, client=client)
    client.export_disambiguations.assert_called_once_with(resolved_only=False)
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "q1" in lines[0]


class TestTokenCommands:
    def test_create_routes_and_parses_csv(self, capsys):
        client = MagicMock()
        client.create_token.return_value = {
            "name": "agente-x",
            "scopes": ["read"],
            "allowed_contexts": ["ctx-a"],
            "token": "kos_abc",
        }
        args = argparse.Namespace(name="agente-x", scopes="read", contexts="ctx-a")
        memory_commands.cmd_token_create(args, client=client)
        client.create_token.assert_called_once_with("agente-x", ["read"], allowed_contexts=["ctx-a"])
        assert "kos_abc" in capsys.readouterr().out

    def test_list_routes(self, capsys):
        client = MagicMock()
        client.list_tokens.return_value = []
        memory_commands.cmd_token_list(argparse.Namespace(), client=client)
        client.list_tokens.assert_called_once_with()

    def test_revoke_routes(self, capsys):
        client = MagicMock()
        memory_commands.cmd_token_revoke(argparse.Namespace(name="agente-x"), client=client)
        client.revoke_token.assert_called_once_with("agente-x")


class TestImportMarkdown:
    def test_missing_path_exits(self, tmp_path):
        args = argparse.Namespace(path=str(tmp_path / "does-not-exist"), context="ctx", dry_run=False)
        with pytest.raises(SystemExit):
            memory_commands.cmd_import_markdown(args)

    def test_dry_run_does_not_call_client(self, tmp_path, capsys):
        md = tmp_path / "notas.md"
        md.write_text("# Titulo\n\nAlgo de contenido para importar aqui.\nSegunda linea de contenido real.\n", encoding="utf-8")
        client = MagicMock()
        args = argparse.Namespace(path=str(tmp_path), context="ctx", dry_run=True, type_=None)
        memory_commands.cmd_import_markdown(args, client=client)
        client.create_memory.assert_not_called()
        assert "dry-run" in capsys.readouterr().out

    def test_imports_new_memory_when_context_exists(self, tmp_path, capsys):
        md = tmp_path / "notas.md"
        md.write_text(
            "# Un titulo\n\nContenido nuevo que no existe todavia en el sistema.\n"
            "Segunda linea de contenido real para pasar el minimo del parser.\n",
            encoding="utf-8",
        )
        client = MagicMock()
        client.list_contexts.return_value = [{"slug": "ctx"}]
        client.search_memories.return_value = {"results": []}
        client.create_memory.return_value = {"id": "m1"}
        args = argparse.Namespace(
            path=str(tmp_path), context="ctx", dry_run=False, type_=None, create_context=False, context_description=None
        )
        memory_commands.cmd_import_markdown(args, client=client)
        assert client.create_memory.call_count == 1
        out = capsys.readouterr().out
        assert "importada" in out

    def test_missing_context_without_create_flag_exits(self, tmp_path):
        md = tmp_path / "notas.md"
        md.write_text("# T\n\nPrimera linea.\nSegunda linea de contenido real.\n", encoding="utf-8")
        client = MagicMock()
        client.list_contexts.return_value = []
        args = argparse.Namespace(
            path=str(tmp_path), context="no-existe", dry_run=False, type_=None, create_context=False, context_description=None
        )
        with pytest.raises(SystemExit):
            memory_commands.cmd_import_markdown(args, client=client)
