"""Interactive layer on top of the argparse tree (Phase 1A).

The argparse tree in `main.py` stays the single source of truth for what commands
exist. This module never re-declares them: it *walks* the parser to build menus
(`cerebro` alone, `cerebro docs` alone) and to ask for whatever required argument is
missing, then hands back a plain argv list that goes through the normal
`parse_args` -> `args.func(args)` path. So the one-shot form and the interactive form
cannot drift apart.

Interactivity only happens with a real terminal on stdin AND stdout and without
`--no-interactive`; otherwise nothing is ever prompted and behaviour is exactly the
scripted one (clear error + exit code).
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

import questionary
from rich.console import Console

from cerebro_cli import tokens

console = Console()

# Commands that work without a session (everything else needs one).
SESSION_FREE = {"login", "logout"}
BACK = "__back__"
EXIT = "__exit__"
NO_FLAG = "--no-interactive"


def is_interactive(argv: list[str]) -> bool:
    if NO_FLAG in argv or os.environ.get("CEREBRO_NO_INTERACTIVE"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


# --------------------------------------------------------------------------- parser walking


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _choices(sub: argparse._SubParsersAction) -> list[tuple[str, str]]:
    helps = {a.dest: (a.help or "") for a in sub._choices_actions}
    return [(name, helps.get(name, "")) for name in sub.choices]


def descend(parser: argparse.ArgumentParser, tokens_: list[str]) -> tuple[argparse.ArgumentParser, list[str], list[str]]:
    """Follow the leading command words of `tokens_` down the tree. Returns the parser
    reached, the words consumed and the tokens left over."""
    path: list[str] = []
    rest = list(tokens_)
    current = parser
    while rest:
        sub = _subparsers_action(current)
        if sub is None or rest[0] not in sub.choices:
            break
        path.append(rest[0])
        current = sub.choices[rest[0]]
        rest = rest[1:]
    return current, path, rest


def _ask(question: Any) -> Any:
    answer = question.ask()
    if answer is None:  # Ctrl-C / Ctrl-D
        raise KeyboardInterrupt
    return answer


def menu(parser: argparse.ArgumentParser, path: list[str], *, has_session: bool, root: bool) -> list[str] | None:
    """Show the menu of `parser` (a node with subcommands) and keep descending until a
    leaf is chosen. Returns the full command path, or None if the user left."""
    while True:
        sub = _subparsers_action(parser)
        assert sub is not None
        entries = [(n, h) for n, h in _choices(sub) if has_session or n in SESSION_FREE or path]
        if not has_session and not path:
            entries = [(n, h) for n, h in entries if n == "login"]
        choices = [questionary.Choice(f"{n}  -  {h}" if h else n, value=n) for n, h in entries]
        choices.append(questionary.Choice("Quit" if root else "Back", value=EXIT if root else BACK))
        title = "cerebro" + ("".join(f" {p}" for p in path))
        picked = _ask(questionary.select(f"{title}: what do you want to do?", choices=choices))
        if picked == EXIT:
            return None
        if picked == BACK:
            return [BACK]
        child = sub.choices[picked]
        if _subparsers_action(child) is None:
            return path + [picked]
        result = menu(child, path + [picked], has_session=has_session, root=False)
        if result == [BACK]:
            continue
        return result


# --------------------------------------------------------------------------- argument prompts


@contextmanager
def _relaxed(parser: argparse.ArgumentParser) -> Iterator[None]:
    """Temporarily make every required argument optional, so a partial command line
    can be parsed to find out what is missing."""
    saved: list[tuple[argparse.Action, Any, Any]] = []
    for a in parser._actions:
        if isinstance(a, argparse._HelpAction):
            continue
        if not a.option_strings and a.nargs is None:
            saved.append((a, a.nargs, a.required))
            a.nargs = "?"
            a.required = False
        elif a.option_strings and a.required:
            saved.append((a, a.nargs, a.required))
            a.required = False
    try:
        yield
    finally:
        for a, nargs, required in saved:
            a.nargs, a.required = nargs, required


def _is_secret(action: argparse.Action) -> bool:
    return any(w in action.dest for w in ("password", "token"))


def _label(action: argparse.Action) -> str:
    name = action.metavar or action.dest.replace("_", "-")
    return f"{name}" + (f" ({action.help})" if action.help and len(action.help) < 70 else "")


def _prompt_for(action: argparse.Action) -> str | None:
    label = _label(action)
    if action.choices:
        return _ask(questionary.select(f"{label}:", choices=[str(c) for c in action.choices]))
    if _is_secret(action):
        return _ask(questionary.password(f"{label}:"))
    required = action.required or not action.option_strings
    validate = (lambda v: bool(v.strip()) or "Required") if required else None
    answer = _ask(questionary.text(f"{label}:", validate=validate) if validate else questionary.text(f"{label}:"))
    return answer.strip() or None


def complete(leaf: argparse.ArgumentParser, words: list[str], rest: list[str]) -> list[str]:
    """Return `rest` extended with prompts for every required argument of `leaf`
    that it does not already provide."""
    with _relaxed(leaf):
        try:
            ns, _ = leaf.parse_known_args(rest)
        except SystemExit:
            return rest
    extra: list[str] = []
    for a in leaf._actions:
        if isinstance(a, argparse._HelpAction) or a.dest in ("func",):
            continue
        required = a.required or (not a.option_strings and a.nargs is None)
        if not required or getattr(ns, a.dest, None) not in (None, []):
            continue
        if isinstance(a, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            continue
        value = _prompt_for(a)
        if value is None:
            continue
        extra += [a.option_strings[0], value] if a.option_strings else [value]
    return rest + extra


# --------------------------------------------------------------------------- login flow


def login_argv() -> list[str]:
    """Ask how to log in and return the argv for `cerebro login ...`."""
    cfg = tokens.load_login_config() or {}
    url = _ask(
        questionary.text(
            "Cerebro gateway URL (e.g. https://cerebro.example.com):",
            default=str(cfg.get("url") or os.environ.get("CEREBRO_GATEWAY_URL") or ""),
        )
    ).strip()
    method = _ask(
        questionary.select(
            "How do you want to log in?",
            choices=[
                questionary.Choice("Username and password", value="user"),
                questionary.Choice("Token", value="token"),
            ],
        )
    )
    argv = ["login"]
    if url:
        argv += ["--url", url]
    if method == "user":
        name = _ask(questionary.text("Username:", validate=lambda v: bool(v.strip()) or "Required")).strip()
        password = _ask(questionary.password("Password:"))
        os.environ["CEREBRO_PASSWORD"] = password  # in-process only; never written anywhere
        argv += ["--user", name]
    else:
        argv += ["--token", _ask(questionary.password("Token:"))]
    return argv


def has_session() -> bool:
    return tokens.session_state() == "valid"


def banner(state: str) -> None:
    if state == "expired":
        console.print("[yellow]Your session has expired: log in again.[/yellow]")
    elif state == "none":
        console.print("[bold]cerebro[/bold]: you are not logged in. Log in to continue.")
