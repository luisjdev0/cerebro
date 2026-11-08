"""Phase 1A: user/password login, logout, set-password, session state and the
interactive layer (menus/prompts are faked; no real terminal or network)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError

from cerebro_cli import auth_commands, interactive, main as cli_main, tokens


def _api_error(status, detail="boom"):
    return CerebroAPIError(status, detail, response=MagicMock())


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "STATE_DIR", tmp_path / ".cerebro")
    monkeypatch.setattr(tokens, "CONFIG_PATH", tmp_path / ".cerebro" / "config.json")
    monkeypatch.delenv("CEREBRO_TOKEN", raising=False)
    monkeypatch.delenv("CEREBRO_PASSWORD", raising=False)
    monkeypatch.delenv("CEREBRO_NO_INTERACTIVE", raising=False)
    return tmp_path / ".cerebro"


def _login_args(**kw):
    base = dict(user=None, token=None, password=None, password_stdin=False, url=None)
    base.update(kw)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- password login


class TestPasswordLogin:
    def test_inline_password_saves_a_session(self, isolated, capsys):
        client = MagicMock()
        expires = "2030-01-01T00:00:00+00:00"
        client.login_password.return_value = {
            "name": "ana", "access_level": "user", "allowed_modules": ["docs"], "token": "cbra_x", "expires_at": expires,
        }
        auth_commands.cmd_login(_login_args(user="ana", password="s3cret-pass-1", url="http://gw"), client=client)
        client.login_password.assert_called_once_with("ana", "s3cret-pass-1")
        saved = json.loads((isolated / "config.json").read_text())
        assert saved == {"token": "cbra_x", "kind": "session", "url": "http://gw", "user": "ana", "expires_at": expires}
        assert "ana" in capsys.readouterr().out

    def test_password_from_environment(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_PASSWORD", "from-the-env-123")
        client = MagicMock()
        client.login_password.return_value = {"name": "ana", "token": "t", "expires_at": None}
        auth_commands.cmd_login(_login_args(user="ana"), client=client)
        client.login_password.assert_called_once_with("ana", "from-the-env-123")

    def test_password_from_stdin(self, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("piped-password-1\n"))
        client = MagicMock()
        client.login_password.return_value = {"name": "ana", "token": "t", "expires_at": None}
        auth_commands.cmd_login(_login_args(user="ana", password_stdin=True), client=client)
        client.login_password.assert_called_once_with("ana", "piped-password-1")

    def test_no_terminal_and_no_password_fails_without_prompting(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        with pytest.raises(SystemExit):
            auth_commands.cmd_login(_login_args(user="ana"), client=MagicMock())

    def test_bad_credentials_and_lockout_write_nothing(self, isolated, capsys):
        for status, text in ((401, "credenciales"), (429, "bloqueada")):
            client = MagicMock()
            client.login_password.side_effect = _api_error(status)
            with pytest.raises(SystemExit):
                auth_commands.cmd_login(_login_args(user="ana", password="whatever-pass"), client=client)
            assert text in capsys.readouterr().err
        assert not (isolated / "config.json").exists()

    @pytest.mark.parametrize("kw", [{}, {"user": "a", "token": "b"}])
    def test_needs_exactly_one_of_user_or_token(self, kw):
        with pytest.raises(SystemExit):
            auth_commands.cmd_login(_login_args(**kw), client=MagicMock())


class TestLogout:
    def test_session_is_revoked_server_side_and_forgotten(self, isolated):
        tokens.save_login_config("cbra_x", "http://gw", kind="session", user="ana", expires_at="2030-01-01T00:00:00+00:00")
        client = MagicMock()
        auth_commands.cmd_logout(argparse.Namespace(), client=client)
        client.logout.assert_called_once()
        assert not (isolated / "config.json").exists()

    def test_pasted_token_is_only_forgotten(self, isolated):
        tokens.save_login_config("cbr_x", None)
        client = MagicMock()
        auth_commands.cmd_logout(argparse.Namespace(), client=client)
        client.logout.assert_not_called()
        assert not (isolated / "config.json").exists()

    def test_unreachable_server_still_clears_local_credentials(self, isolated):
        from cerebro_clients import CerebroConnectionError

        tokens.save_login_config("cbra_x", None, kind="session")
        client = MagicMock()
        client.logout.side_effect = CerebroConnectionError("http://x", OSError("down"))
        auth_commands.cmd_logout(argparse.Namespace(), client=client)
        assert not (isolated / "config.json").exists()

    def test_nothing_saved(self, capsys):
        auth_commands.cmd_logout(argparse.Namespace(), client=MagicMock())
        assert "No hay" in capsys.readouterr().out


# --------------------------------------------------------------------------- users


class TestUserPasswords:
    def test_create_with_password_sends_it(self):
        client = MagicMock()
        client.create_user.return_value = {"name": "ana", "id": "1", "access_level": "user"}
        args = argparse.Namespace(
            name="ana", email=None, access_level="user", password="long-enough-pw", password_stdin=False
        )
        # confirm=True only matters for prompts; inline values pass straight through
        auth_commands.cmd_user_create(args, client=client)
        client.create_user.assert_called_once_with("ana", email=None, access_level="user", password="long-enough-pw")

    def test_set_password_calls_the_client(self):
        client = MagicMock()
        args = argparse.Namespace(
            name="ana", password="new-long-password", password_stdin=False, current_password="old-long-password"
        )
        auth_commands.cmd_user_set_password(args, client=client)
        client.set_password.assert_called_once_with("ana", "new-long-password", current_password="old-long-password")

    @pytest.mark.parametrize("status,text", [(403, "No autorizado"), (429, "bloqueada")])
    def test_set_password_errors(self, status, text, capsys):
        client = MagicMock()
        client.set_password.side_effect = _api_error(status)
        args = argparse.Namespace(name="ana", password="new-long-password", password_stdin=False, current_password=None)
        with pytest.raises(SystemExit):
            auth_commands.cmd_user_set_password(args, client=client)
        assert text in capsys.readouterr().err

    def test_parsing(self):
        p = cli_main.build_parser()
        a = p.parse_args(["user", "set-password", "ana", "--password", "--current-password"])
        assert a.func is auth_commands.cmd_user_set_password
        assert a.password == auth_commands.ASK and a.current_password == auth_commands.ASK
        a = p.parse_args(["login", "--user", "ana", "--password=abc"])
        assert a.password == "abc"


# --------------------------------------------------------------------------- session state


class TestSessionState:
    def test_none_valid_expired(self, monkeypatch):
        assert tokens.session_state() == "none"
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tokens.save_login_config("t", None, kind="session", expires_at=future)
        assert tokens.session_state() == "valid"
        tokens.save_login_config("t", None, kind="session", expires_at=past)
        assert tokens.session_state() == "expired"
        tokens.save_login_config("t", None)  # no expiry
        assert tokens.session_state() == "valid"

    def test_environment_token_counts_as_session(self, monkeypatch):
        monkeypatch.setenv("CEREBRO_TOKEN", "x")
        assert tokens.session_state() == "valid"

    def test_expired_session_is_reported_before_running_a_command(self, capsys):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        tokens.save_login_config("t", None, kind="session", expires_at=past)
        with pytest.raises(SystemExit):
            cli_main.main(["docs", "list", "--no-interactive"])
        assert "caducado" in capsys.readouterr().err


# --------------------------------------------------------------------------- interactive layer


class FakeQ:
    """Replaces questionary: each factory returns an object whose .ask() pops the next answer."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def _make(self, message, **kw):
        self.asked.append((message, kw))
        return MagicMock(ask=lambda: self.answers.pop(0))

    def install(self, monkeypatch):
        monkeypatch.setattr(interactive.questionary, "select", self._make)
        monkeypatch.setattr(interactive.questionary, "text", self._make)
        monkeypatch.setattr(interactive.questionary, "password", self._make)


class TestInteractive:
    def test_no_tty_never_prompts(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        assert interactive.is_interactive(["docs"]) is False

    def test_flag_and_env_disable_prompts(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        monkeypatch.setattr("sys.stdout", MagicMock(isatty=lambda: True))
        assert interactive.is_interactive(["docs"]) is True
        assert interactive.is_interactive(["docs", "--no-interactive"]) is False
        monkeypatch.setenv("CEREBRO_NO_INTERACTIVE", "1")
        assert interactive.is_interactive(["docs"]) is False

    def test_descend_stops_at_the_deepest_known_node(self):
        parser = cli_main.build_parser()
        leaf, path, rest = interactive.descend(parser, ["docs", "category", "create", "x"])
        assert path == ["docs", "category", "create"] and rest == ["x"]
        node, path, _ = interactive.descend(parser, ["docs"])
        assert path == ["docs"] and interactive._subparsers_action(node) is not None

    def test_menu_without_session_offers_only_login(self, monkeypatch):
        fq = FakeQ(["login"])
        fq.install(monkeypatch)
        parser = cli_main.build_parser()
        assert interactive.menu(parser, [], has_session=False, root=True) == ["login"]
        choices = fq.asked[0][1]["choices"]
        values = [c.value for c in choices]
        assert values == ["login", interactive.EXIT]

    def test_full_menu_descends_into_submenus_and_back(self, monkeypatch):
        fq = FakeQ(["docs", interactive.BACK, "docs", "category", "list"])
        fq.install(monkeypatch)
        parser = cli_main.build_parser()
        assert interactive.menu(parser, [], has_session=True, root=True) == ["docs", "category", "list"]

    def test_missing_required_arguments_are_asked(self, monkeypatch):
        fq = FakeQ(["mi-doc", "ecosistema"])
        fq.install(monkeypatch)
        parser = cli_main.build_parser()
        leaf, path, rest = interactive.descend(parser, ["docs", "get"])
        out = interactive.complete(leaf, path, rest)
        assert out[0] == "mi-doc" or "mi-doc" in out
        parser.parse_args(path + out)  # the completed line is valid

    def test_secret_arguments_use_a_password_prompt(self, monkeypatch):
        fq = FakeQ(["a-secret-value"])
        calls = []
        monkeypatch.setattr(interactive.questionary, "password", lambda m, **k: (calls.append(m), MagicMock(ask=lambda: fq.answers.pop(0)))[1])
        action = argparse.Action(option_strings=["--password"], dest="password")
        assert interactive._prompt_for(action) == "a-secret-value"
        assert calls

    def test_main_without_session_and_without_tty_fails_cleanly(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        with pytest.raises(SystemExit) as exc:
            cli_main.main([])
        assert exc.value.code == 2  # argparse usage error, as before

    def test_main_interactive_without_session_forces_login_first(self, monkeypatch):
        monkeypatch.setattr(interactive, "is_interactive", lambda argv: True)
        calls = []
        monkeypatch.setattr(interactive, "login_argv", lambda: ["login", "--token", "cbr_t"])
        monkeypatch.setattr(auth_commands, "cmd_login", lambda a: (calls.append(a.token), tokens.save_login_config("cbr_t", None)))
        FakeQ([interactive.EXIT]).install(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cli_main.main([])
        assert calls == ["cbr_t"] and exc.value.code == 0
