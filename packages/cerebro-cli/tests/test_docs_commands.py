"""`cerebro docs <subcommand>` must route to the correct `DocsClient` -- with no
business logic of its own beyond reading file/stdin content and formatting output."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError

from cerebro_cli import docs_commands


def _api_error(status_code, detail="boom"):
    return CerebroAPIError(status_code, detail, response=MagicMock())


def test_category_create_defaults_name_to_slug(capsys):
    client = MagicMock()
    client.create_category.return_value = {"slug": "eco", "hidden": False, "locked": False}
    args = argparse.Namespace(slug="eco", name=None, description="d", hidden=False, locked=False)
    docs_commands.cmd_category_create(args, client=client)
    client.create_category.assert_called_once_with("eco", "eco", description="d", hidden=False, locked=False)


def test_category_create_hidden_locked(capsys):
    client = MagicMock()
    client.create_category.return_value = {"slug": "eco", "hidden": True, "locked": True}
    args = argparse.Namespace(slug="eco", name=None, description=None, hidden=True, locked=True)
    docs_commands.cmd_category_create(args, client=client)
    client.create_category.assert_called_once_with("eco", "eco", description=None, hidden=True, locked=True)
    assert "oculta, bloqueada" in capsys.readouterr().out


def test_category_list_prints(capsys):
    client = MagicMock()
    client.list_categories.return_value = [{"slug": "eco", "name": "Ecosistema", "description": None}]
    docs_commands.cmd_category_list(argparse.Namespace(), client=client)
    assert "eco" in capsys.readouterr().out


def test_category_rename_routes_to_update(capsys):
    client = MagicMock()
    client.update_category.return_value = {"slug": "eco-nuevo"}
    args = argparse.Namespace(slug="eco", new_slug="eco-nuevo", name=None, description=None, hidden=False, visible=False)
    docs_commands.cmd_category_rename(args, client=client)
    client.update_category.assert_called_once_with(
        "eco", new_slug="eco-nuevo", name=None, description=None, hidden=None
    )


def test_category_rename_hidden_flag_sets_hidden_true(capsys):
    client = MagicMock()
    client.update_category.return_value = {"slug": "eco"}
    args = argparse.Namespace(slug="eco", new_slug="eco", name=None, description=None, hidden=True, visible=False)
    docs_commands.cmd_category_rename(args, client=client)
    client.update_category.assert_called_once_with("eco", new_slug="eco", name=None, description=None, hidden=True)


def test_category_rename_visible_flag_sets_hidden_false(capsys):
    client = MagicMock()
    client.update_category.return_value = {"slug": "eco"}
    args = argparse.Namespace(slug="eco", new_slug="eco", name=None, description=None, hidden=False, visible=True)
    docs_commands.cmd_category_rename(args, client=client)
    client.update_category.assert_called_once_with("eco", new_slug="eco", name=None, description=None, hidden=False)


def test_category_delete_routes(capsys):
    client = MagicMock()
    client.delete_category.return_value = {"slug": "eco", "status": "deleted", "documents_deleted": 3}
    args = argparse.Namespace(slug="eco", force=True)
    docs_commands.cmd_category_delete(args, client=client)
    client.delete_category.assert_called_once_with("eco", force=True)
    assert "3" in capsys.readouterr().out


class TestSave:
    def test_reads_content_from_file(self, tmp_path, capsys):
        content_file = tmp_path / "doc.md"
        content_file.write_text("# Contenido\n\nTexto.", encoding="utf-8")
        client = MagicMock()
        client.create_document.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
        args = argparse.Namespace(category="eco", title="Titulo", content_file=str(content_file), slug=None)
        docs_commands.cmd_save(args, client=client)
        client.create_document.assert_called_once_with("Titulo", "# Contenido\n\nTexto.", "eco", slug=None)

    def test_unknown_category_error_propagates(self, tmp_path):
        content_file = tmp_path / "doc.md"
        content_file.write_text("contenido", encoding="utf-8")
        client = MagicMock()
        client.create_document.side_effect = _api_error(404, "categoria inexistente")
        args = argparse.Namespace(category="no-existe", title="T", content_file=str(content_file), slug=None)
        with pytest.raises(SystemExit):
            docs_commands.cmd_save(args, client=client)


def test_get_prints_content(capsys):
    client = MagicMock()
    client.get_document.return_value = {"content": "el contenido completo"}
    args = argparse.Namespace(category="eco", slug="mi-doc")
    docs_commands.cmd_get(args, client=client)
    client.get_document.assert_called_once_with("eco", "mi-doc")
    assert "el contenido completo" in capsys.readouterr().out


def test_list_routes_without_q(capsys):
    client = MagicMock()
    client.list_documents.return_value = []
    args = argparse.Namespace(category="eco", archived=False, limit=20, offset=0)
    docs_commands.cmd_list(args, client=client)
    client.list_documents.assert_called_once_with(category="eco", limit=20, offset=0)


def test_list_archived_routes_to_list_archived_documents(capsys):
    client = MagicMock()
    client.list_archived_documents.return_value = []
    args = argparse.Namespace(category="eco", archived=True, limit=20, offset=0)
    docs_commands.cmd_list(args, client=client)
    client.list_archived_documents.assert_called_once_with(category="eco", limit=20, offset=0)
    client.list_documents.assert_not_called()


def test_search_routes_with_q(capsys):
    client = MagicMock()
    client.list_documents.return_value = []
    args = argparse.Namespace(query="busqueda", category="eco", limit=20, offset=0)
    docs_commands.cmd_search(args, client=client)
    client.list_documents.assert_called_once_with(category="eco", q="busqueda", limit=20, offset=0)


def test_update_reads_content_from_file(tmp_path, capsys):
    content_file = tmp_path / "doc.md"
    content_file.write_text("nuevo contenido", encoding="utf-8")
    client = MagicMock()
    client.update_document.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
    args = argparse.Namespace(
        document_id="d1", title="Nuevo titulo", category="eco", content_file=str(content_file), slug=None
    )
    docs_commands.cmd_update(args, client=client)
    client.update_document.assert_called_once_with("d1", "Nuevo titulo", "nuevo contenido", "eco", slug=None)


class TestPatchSection:
    def test_delete_operation_ignores_body(self, capsys):
        client = MagicMock()
        client.patch_section.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
        args = argparse.Namespace(
            document_id="d1",
            heading="Vieja seccion",
            operation="delete",
            body=None,
            body_file=None,
            create_if_missing=False,
            new_heading_level=2,
        )
        docs_commands.cmd_patch_section(args, client=client)
        client.patch_section.assert_called_once_with(
            "d1", "Vieja seccion", "delete", body="", create_if_missing=False, new_heading_level=2
        )

    def test_body_flag_is_used_directly(self, capsys):
        client = MagicMock()
        client.patch_section.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
        args = argparse.Namespace(
            document_id="d1",
            heading="Intro",
            operation="append",
            body="texto nuevo",
            body_file=None,
            create_if_missing=False,
            new_heading_level=2,
        )
        docs_commands.cmd_patch_section(args, client=client)
        assert client.patch_section.call_args.kwargs["body"] == "texto nuevo"

    def test_ambiguous_heading_error_propagates(self):
        client = MagicMock()
        client.patch_section.side_effect = _api_error(409, "heading 'Intro' is ambiguous")
        args = argparse.Namespace(
            document_id="d1",
            heading="Intro",
            operation="replace",
            body="x",
            body_file=None,
            create_if_missing=False,
            new_heading_level=2,
        )
        with pytest.raises(SystemExit):
            docs_commands.cmd_patch_section(args, client=client)


class TestDelete:
    def test_asks_confirmation_and_cancels(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr("builtins.input", lambda _: "no")
        args = argparse.Namespace(document_id="d1", yes=False)
        docs_commands.cmd_delete(args, client=client)
        client.delete_document.assert_not_called()

    def test_yes_flag_skips_confirmation(self):
        client = MagicMock()
        args = argparse.Namespace(document_id="d1", yes=True)
        docs_commands.cmd_delete(args, client=client)
        client.delete_document.assert_called_once_with("d1")


def test_stats_prints_counts(capsys):
    client = MagicMock()
    client.get_stats.return_value = {"categories": 2, "documents": 5, "versions": 1}
    docs_commands.cmd_stats(argparse.Namespace(), client=client)
    out = capsys.readouterr().out
    assert "2" in out and "5" in out and "1" in out


def test_archive_routes_to_client(capsys):
    client = MagicMock()
    client.archive_document.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
    docs_commands.cmd_archive(argparse.Namespace(document_id="d1"), client=client)
    client.archive_document.assert_called_once_with("d1")
    assert "archivado" in capsys.readouterr().out


def test_unarchive_routes_to_client(capsys):
    client = MagicMock()
    client.unarchive_document.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
    docs_commands.cmd_unarchive(argparse.Namespace(document_id="d1"), client=client)
    client.unarchive_document.assert_called_once_with("d1")
    assert "desarchivado" in capsys.readouterr().out


def test_history_prints_versions(capsys):
    client = MagicMock()
    client.get_document_versions.return_value = [
        {"version_number": 2, "category": "eco", "title": "T", "content": "linea 1\nlinea 2", "created_at": "2026-09-23"},
    ]
    docs_commands.cmd_history(argparse.Namespace(document_id="d1"), client=client)
    client.get_document_versions.assert_called_once_with("d1")
    out = capsys.readouterr().out
    assert "v2" in out and "eco" in out and "linea 1" in out


def test_history_empty_prints_placeholder(capsys):
    client = MagicMock()
    client.get_document_versions.return_value = []
    docs_commands.cmd_history(argparse.Namespace(document_id="d1"), client=client)
    assert "sin versiones" in capsys.readouterr().out


class TestImportMarkdown:
    def test_dry_run_lists_without_calling_api(self, tmp_path, capsys):
        (tmp_path / "guia.md").write_text("# Guia real\n\ncontenido", encoding="utf-8")
        client = MagicMock()
        args = argparse.Namespace(path=str(tmp_path), category="eco", dry_run=True, update=False)
        docs_commands.cmd_import_markdown(args, client=client)
        client.get_document.assert_not_called()
        client.create_document.assert_not_called()
        out = capsys.readouterr().out
        assert "eco/guia" in out and "Guia real" in out

    def test_imports_new_file(self, tmp_path):
        (tmp_path / "notas.md").write_text("contenido sin heading", encoding="utf-8")
        client = MagicMock()
        client.get_document.side_effect = _api_error(404)
        client.create_document.return_value = {"id": "d1"}
        args = argparse.Namespace(path=str(tmp_path), category="eco", dry_run=False, update=False)
        docs_commands.cmd_import_markdown(args, client=client)
        client.create_document.assert_called_once_with(
            "notas", "contenido sin heading", "eco", slug="notas"
        )

    def test_skips_existing_by_default(self, tmp_path, capsys):
        (tmp_path / "notas.md").write_text("contenido", encoding="utf-8")
        client = MagicMock()
        client.get_document.return_value = {"id": "d1"}
        args = argparse.Namespace(path=str(tmp_path), category="eco", dry_run=False, update=False)
        docs_commands.cmd_import_markdown(args, client=client)
        client.create_document.assert_not_called()
        client.update_document.assert_not_called()
        assert "ya existe" in capsys.readouterr().out

    def test_update_flag_updates_existing(self, tmp_path):
        (tmp_path / "notas.md").write_text("contenido nuevo", encoding="utf-8")
        client = MagicMock()
        client.get_document.return_value = {"id": "d1"}
        args = argparse.Namespace(path=str(tmp_path), category="eco", dry_run=False, update=True)
        docs_commands.cmd_import_markdown(args, client=client)
        client.update_document.assert_called_once_with("d1", "notas", "contenido nuevo", "eco", slug="notas")

    def test_missing_path_exits(self, tmp_path):
        client = MagicMock()
        args = argparse.Namespace(path=str(tmp_path / "no-existe"), category="eco", dry_run=False, update=False)
        with pytest.raises(SystemExit):
            docs_commands.cmd_import_markdown(args, client=client)
