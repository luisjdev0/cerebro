"""Smoke tests del arbol de subcomandos: cada linea de la especificacion
(ecosistema-cerebro.md SS11 + la tarea) debe parsear y despachar a la funcion
correcta, sin ejecutar ninguna llamada de red real."""

from __future__ import annotations

from cerebro_cli import docs_commands, memory_commands, shared_commands
from cerebro_cli.main import build_parser


def _parse(argv):
    return build_parser().parse_args(argv)


class TestMemorySubcommands:
    def test_stats(self):
        args = _parse(["memory", "stats"])
        assert args.func is memory_commands.cmd_stats

    def test_export_disambiguations(self):
        args = _parse(["memory", "export-disambiguations", "--resolved-only"])
        assert args.func is memory_commands.cmd_export_disambiguations
        assert args.resolved_only is True

    def test_import_markdown(self):
        args = _parse(["memory", "import-markdown", "./notas", "--context", "notas-personales"])
        assert args.func is memory_commands.cmd_import_markdown
        assert args.context == "notas-personales"

    def test_token_create(self):
        args = _parse(["memory", "token", "create", "claude-code", "--scopes", "read,write"])
        assert args.func is memory_commands.cmd_token_create

    def test_token_list(self):
        args = _parse(["memory", "token", "list"])
        assert args.func is memory_commands.cmd_token_list

    def test_token_revoke(self):
        args = _parse(["memory", "token", "revoke", "claude-code"])
        assert args.func is memory_commands.cmd_token_revoke


class TestDocsSubcommands:
    def test_category_create(self):
        args = _parse(["docs", "category", "create", "ecosistema", "--description", "algo"])
        assert args.func is docs_commands.cmd_category_create

    def test_category_list(self):
        args = _parse(["docs", "category", "list"])
        assert args.func is docs_commands.cmd_category_list

    def test_category_rename(self):
        args = _parse(["docs", "category", "rename", "ecosistema-cerebro", "ecosistema"])
        assert args.func is docs_commands.cmd_category_rename

    def test_category_delete(self):
        args = _parse(["docs", "category", "delete", "ecosistema", "--force"])
        assert args.func is docs_commands.cmd_category_delete

    def test_save(self):
        args = _parse(["docs", "save", "ecosistema", "Mi titulo"])
        assert args.func is docs_commands.cmd_save

    def test_get(self):
        args = _parse(["docs", "get", "ecosistema", "mi-doc"])
        assert args.func is docs_commands.cmd_get

    def test_list(self):
        args = _parse(["docs", "list", "--category", "ecosistema"])
        assert args.func is docs_commands.cmd_list

    def test_search(self):
        args = _parse(["docs", "search", "algo"])
        assert args.func is docs_commands.cmd_search

    def test_update(self):
        args = _parse(["docs", "update", "doc-1", "Titulo", "ecosistema"])
        assert args.func is docs_commands.cmd_update

    def test_patch_section(self):
        args = _parse(["docs", "patch-section", "doc-1", "Intro", "replace"])
        assert args.func is docs_commands.cmd_patch_section

    def test_delete(self):
        args = _parse(["docs", "delete", "doc-1", "--yes"])
        assert args.func is docs_commands.cmd_delete

    def test_stats(self):
        args = _parse(["docs", "stats"])
        assert args.func is docs_commands.cmd_stats


class TestSharedSubcommands:
    def test_backup(self):
        args = _parse(["backup"])
        assert args.func is shared_commands.cmd_backup

    def test_restore(self):
        args = _parse(["restore", "backup.sql"])
        assert args.func is shared_commands.cmd_restore

    def test_token_create_transversal(self):
        args = _parse(["token", "create", "agente-x", "--scopes", "read,write"])
        assert args.func is shared_commands.cmd_token_create

    def test_token_revoke_transversal(self):
        args = _parse(["token", "revoke", "agente-x"])
        assert args.func is shared_commands.cmd_token_revoke
