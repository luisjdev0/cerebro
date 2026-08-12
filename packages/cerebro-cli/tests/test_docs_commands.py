"""`cerebro docs <subcomando>` debe enrutar al `DocsClient` correcto -- sin logica de
negocio propia mas alla de leer contenido de archivo/stdin y formatear la salida."""

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
    client.create_category.return_value = {"slug": "eco"}
    args = argparse.Namespace(slug="eco", name=None, description="d")
    docs_commands.cmd_category_create(args, client=client)
    client.create_category.assert_called_once_with("eco", "eco", description="d")


def test_category_list_prints(capsys):
    client = MagicMock()
    client.list_categories.return_value = [{"slug": "eco", "name": "Ecosistema", "description": None}]
    docs_commands.cmd_category_list(argparse.Namespace(), client=client)
    assert "eco" in capsys.readouterr().out


def test_category_rename_routes_to_update(capsys):
    client = MagicMock()
    client.update_category.return_value = {"slug": "eco-nuevo"}
    args = argparse.Namespace(slug="eco", new_slug="eco-nuevo", name=None, description=None)
    docs_commands.cmd_category_rename(args, client=client)
    client.update_category.assert_called_once_with("eco", new_slug="eco-nuevo", name=None, description=None)


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
    args = argparse.Namespace(category="eco", limit=20, offset=0)
    docs_commands.cmd_list(args, client=client)
    client.list_documents.assert_called_once_with(category="eco", limit=20, offset=0)


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
