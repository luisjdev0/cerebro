"""`cerebro flow <subcomando>` debe enrutar al `FlowsClient` correcto -- sin logica de
negocio propia mas alla de leer YAML de archivo y formatear la salida."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError

from cerebro_cli import flow_commands


def _api_error(status_code, detail="boom"):
    return CerebroAPIError(status_code, detail, response=MagicMock())


def test_category_create_defaults_name_to_slug(capsys):
    client = MagicMock()
    client.create_category.return_value = {"slug": "incident", "code": "INC"}
    args = argparse.Namespace(slug="incident", code="INC", name=None, description="d")
    flow_commands.cmd_category_create(args, client=client)
    client.create_category.assert_called_once_with("incident", "INC", "incident", description="d")
    assert "INC" in capsys.readouterr().out


def test_category_list_prints(capsys):
    client = MagicMock()
    client.list_categories.return_value = [{"slug": "incident", "code": "INC", "name": "Incidencias", "description": None}]
    flow_commands.cmd_category_list(argparse.Namespace(), client=client)
    assert "incident" in capsys.readouterr().out


class TestValidate:
    def test_valid_prints_confirmation(self, tmp_path, capsys):
        yaml_file = tmp_path / "flow.yaml"
        yaml_file.write_text("metadata: {}", encoding="utf-8")
        client = MagicMock()
        args = argparse.Namespace(yaml_file=str(yaml_file))
        flow_commands.cmd_validate(args, client=client)
        client.validate_flow.assert_called_once_with("metadata: {}")
        assert "Valido" in capsys.readouterr().out

    def test_invalid_exits_with_detail(self, tmp_path):
        yaml_file = tmp_path / "flow.yaml"
        yaml_file.write_text("metadata: {}", encoding="utf-8")
        client = MagicMock()
        client.validate_flow.side_effect = _api_error(422, "step 'a': falta 'next'")
        args = argparse.Namespace(yaml_file=str(yaml_file))
        with pytest.raises(SystemExit):
            flow_commands.cmd_validate(args, client=client)

    def test_missing_file_exits(self, tmp_path):
        client = MagicMock()
        args = argparse.Namespace(yaml_file=str(tmp_path / "no-existe.yaml"))
        with pytest.raises(SystemExit):
            flow_commands.cmd_validate(args, client=client)


class TestSave:
    def test_reads_yaml_from_file(self, tmp_path, capsys):
        yaml_file = tmp_path / "flow.yaml"
        yaml_file.write_text("metadata: {}", encoding="utf-8")
        client = MagicMock()
        client.create_flow.return_value = {"code": "INC-1", "id": "f1"}
        args = argparse.Namespace(category="incident", yaml_file=str(yaml_file), code=None)
        flow_commands.cmd_save(args, client=client)
        client.create_flow.assert_called_once_with("incident", "metadata: {}", code=None)
        assert "INC-1" in capsys.readouterr().out

    def test_unknown_category_error_propagates(self, tmp_path):
        yaml_file = tmp_path / "flow.yaml"
        yaml_file.write_text("metadata: {}", encoding="utf-8")
        client = MagicMock()
        client.create_flow.side_effect = _api_error(404, "categoria inexistente")
        args = argparse.Namespace(category="no-existe", yaml_file=str(yaml_file), code=None)
        with pytest.raises(SystemExit):
            flow_commands.cmd_save(args, client=client)


def test_get_prints_yaml_content(capsys):
    client = MagicMock()
    client.get_flow.return_value = {"yaml_content": "metadata: {}"}
    args = argparse.Namespace(code="INC-1")
    flow_commands.cmd_get(args, client=client)
    client.get_flow.assert_called_once_with("INC-1")
    assert "metadata: {}" in capsys.readouterr().out


def test_list_routes_to_client(capsys):
    client = MagicMock()
    client.list_flows.return_value = []
    args = argparse.Namespace(category="incident", limit=20, offset=0)
    flow_commands.cmd_list(args, client=client)
    client.list_flows.assert_called_once_with(category="incident", limit=20, offset=0)


def test_update_reads_yaml_from_file(tmp_path, capsys):
    yaml_file = tmp_path / "flow.yaml"
    yaml_file.write_text("metadata: {}", encoding="utf-8")
    client = MagicMock()
    client.update_flow.return_value = {"code": "INC-1", "current_version": 2}
    args = argparse.Namespace(code="INC-1", yaml_file=str(yaml_file))
    flow_commands.cmd_update(args, client=client)
    client.update_flow.assert_called_once_with("INC-1", "metadata: {}")
    assert "v2" in capsys.readouterr().out


class TestDelete:
    def test_asks_confirmation_and_cancels(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr("builtins.input", lambda _: "no")
        args = argparse.Namespace(code="INC-1", yes=False)
        flow_commands.cmd_delete(args, client=client)
        client.delete_flow.assert_not_called()

    def test_yes_flag_skips_confirmation(self):
        client = MagicMock()
        args = argparse.Namespace(code="INC-1", yes=True)
        flow_commands.cmd_delete(args, client=client)
        client.delete_flow.assert_called_once_with("INC-1")


def test_stats_prints_counts(capsys):
    client = MagicMock()
    client.get_stats.return_value = {"categories": 1, "flows": 2, "runs": 3}
    flow_commands.cmd_stats(argparse.Namespace(), client=client)
    out = capsys.readouterr().out
    assert "1" in out and "2" in out and "3" in out
