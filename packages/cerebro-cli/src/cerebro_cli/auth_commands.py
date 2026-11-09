"""`cerebro login` / `cerebro user <subcommand>` / `cerebro group <subcommand>` --
thin wrappers over `AuthClient` (`cerebro_clients`), the ecosystem's single
identity/token service (ecosistema-cerebro.md SS13, updated: cerebro-auth replaces
the old per-service token mechanism).

Like `cerebro token create/revoke` (`shared_commands.py`), these are ecosystem-level
commands with no module prefix -- unlike `memory`/`docs`/`flow`, cerebro-auth isn't a
content module of its own, it's the identity layer the others sit on top of.

`cerebro login` is the only command here that writes anything to disk: on a
successful `AuthClient.login()`, it persists the resolved token/gateway URL to
`~/.cerebro/config.json` (`cerebro_cli.tokens`) so other `cerebro-cli` invocations
don't need `CEREBRO_TOKEN`/`CEREBRO_*_URL` set in the environment --
`cerebro_clients.config` reads this file back as a fallback, below any env var.

`cerebro user create/list` and `cerebro group create/set-scopes/add-member` are
admin-only in practice -- the API enforces that, these commands just call it and
surface whatever error comes back.

`cerebro login` supports two modes: `--token` (a token created by an admin) and
`--user` (+ a password from a prompt, `--password-stdin`, CEREBRO_PASSWORD or the
discouraged inline `--password=VALUE`). A user/password login stores a session token
with an expiry; `cerebro logout` revokes it. There is no MCP tool for passwords on
purpose: a secret must not pass through a model's context.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone

from cerebro_clients import AuthClient, CerebroAPIError, CerebroConnectionError

from cerebro_cli.tokens import clear_login_config, load_login_config, save_login_config


def _client(url: str | None = None) -> AuthClient:
    return AuthClient(base_url=url)


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


# --------------------------------------------------------------------------- secrets

# Sentinel for `--password` given WITHOUT a value (`cerebro login --password`): ask for it.
ASK = "__ask__"
ENV_PASSWORD = "CEREBRO_PASSWORD"


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def _read_password(prompt: str = "Contrasena: ", *, confirm: bool = False) -> str:
    """Interactive, no-echo password prompt (stderr, so stdout stays clean)."""
    if not sys.stdin.isatty():
        _fail("No interactive terminal: use --password-stdin, CEREBRO_PASSWORD or --password=VALUE.")
    value = getpass.getpass(prompt, stream=sys.stderr)
    if confirm and getpass.getpass("Repeat the password: ", stream=sys.stderr) != value:
        _fail("Passwords do not match.")
    if not value:
        _fail("The password cannot be empty.")
    return value


def resolve_password(
    args: argparse.Namespace, *, attr: str = "password", prompt: str = "Contrasena: ", confirm: bool = False
) -> str:
    """Where a password comes from, in order: `--password=VALUE` (inline: it shows up in
    shell history and `ps`, so the help marks it as insecure -- it is here because it is
    sometimes the only option), `--password-stdin`, the CEREBRO_PASSWORD variable, and
    finally a no-echo prompt."""
    value = getattr(args, attr, None)
    if value and value != ASK:
        return value
    if getattr(args, "password_stdin", False) and attr == "password":
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            _fail("No password received on stdin.")
        return line
    env_value = os.environ.get(ENV_PASSWORD)
    if env_value and attr == "password" and value != ASK:
        return env_value
    return _read_password(prompt, confirm=confirm)


# --------------------------------------------------------------------------- login


def _days_until(when: str | None) -> int | None:
    """Whole days from now to an ISO timestamp from the server, or None if it can't be read."""
    if not when:
        return None
    try:
        stamp = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0, round((stamp - datetime.now(timezone.utc)).total_seconds() / 86400))


def cmd_login(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    # `--url` is the GATEWAY's base URL (e.g. https://cerebro.example.com), matching
    # every other CEREBRO_*_URL in this project -- the gateway routes /api/auth/* to
    # cerebro-auth by prefix (gateway/Caddyfile), so the actual connection appends
    # that prefix. The unsuffixed gateway URL is what gets saved to
    # ~/.cerebro/config.json, so cerebro_clients.config's read-fallback can derive
    # each service's own URL (.../api/memory, .../api/docs, .../api/flows, .../api/auth) from it.
    user = getattr(args, "user", None)
    token = getattr(args, "token", None)
    if bool(user) == bool(token):
        _fail("Pass --user (with a password) or --token, but not both and not neither.")

    auth_url = f"{args.url.rstrip('/')}/api/auth" if args.url else None
    client = client or _client(auth_url)
    try:
        if token:
            result = client.login(token)
            session_token, kind, expires_at = token, "token", None
            idle_days = None
        else:
            password = resolve_password(args, prompt=f"Contrasena de {user}: ")
            # A sliding session: every use renews it, and it ends only after a long time without
            # use. The server is the judge, so the expiry is NOT saved (a fixed date on disk would
            # lock out someone who uses the CLI every day).
            result = client.login_password(user, password, remember=True)
            session_token = result["token"]
            kind, expires_at = "session", None
            idle_days = _days_until(result.get("expires_at"))
    except CerebroConnectionError as exc:
        _fail(f"Could not connect to cerebro-auth: {exc}")
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            _fail("Error: invalid credentials." if user else "Error: invalid token.")
        elif exc.status_code == 429:
            _fail("Account temporarily locked after too many failed attempts. Try again later.")
        else:
            _fail(f"The API returned {exc.status_code}: {exc.detail}")

    # Only persist once login actually succeeded - never write a token that was
    # never validated.
    save_login_config(
        session_token, args.url, kind=kind, user=result.get("name"), expires_at=expires_at
    )

    modules = result.get("allowed_modules") or []
    print(
        f"Logged in as '{result.get('name')}' "
        f"(access_level: {result.get('access_level')}; modules: {', '.join(modules) or 'all'})."
    )
    if idle_days:
        print(f"You stay signed in while you use cerebro; the session ends after {idle_days} days without use.")
    print("Credentials saved to ~/.cerebro/config.json.")


def cmd_logout(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    cfg = load_login_config()
    if not cfg or not cfg.get("token"):
        print("There is no saved session.")
        return
    if cfg.get("kind") == "session":
        # Close it server-side too (best effort: an expired/unreachable server must not
        # stop the user from forgetting the credentials locally).
        try:
            (client or _client()).logout()
        except (CerebroConnectionError, CerebroAPIError):
            print("Warning: could not close the session on the server; it is only forgotten locally.", file=sys.stderr)
    clear_login_config()
    print("Logged out; local credentials deleted.")


# --------------------------------------------------------------------------- user


def cmd_user_create(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    password = None
    if getattr(args, "password", None):
        password = resolve_password(args, prompt=f"Password for {args.name}: ", confirm=True)
    try:
        user = client.create_user(
            args.name, email=args.email, access_level=args.access_level, password=password
        )
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(
        f"User '{user.get('name', args.name)}' created "
        f"(id: {user.get('id')}; access_level: {user.get('access_level')}; "
        f"password: {'yes' if password else 'no, token only'})."
    )


def cmd_user_set_password(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    new_password = resolve_password(args, prompt=f"New password for {args.name}: ", confirm=True)
    current = getattr(args, "current_password", None)
    if current == ASK:
        current = _read_password("Current password: ")
    try:
        client.set_password(args.name, new_password, current_password=current)
    except CerebroConnectionError as exc:
        _fail(f"Could not connect to cerebro-auth: {exc}")
    except CerebroAPIError as exc:
        if exc.status_code == 422 and "current_password" in str(exc.detail):
            _fail("To change your own password pass the current one with --current-password.")
        if exc.status_code == 403:
            _fail("Not authorized: wrong current password, or you are not an administrator.")
        if exc.status_code == 429:
            _fail("Account temporarily locked after too many failed attempts. Try again later.")
        _fail(f"The API returned {exc.status_code}: {exc.detail}")
    print(f"Password of '{args.name}' updated; their open sessions have been closed.")


def cmd_user_list(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    try:
        users = client.list_users()
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if not users:
        print("(no users yet)")
        return

    print(f"{'name':<25} {'email':<30} {'access_level':<12} id")
    print("-" * 100)
    for u in users:
        print(f"{u['name']:<25} {(u.get('email') or ''):<30} {u.get('access_level', ''):<12} {u.get('id', '')}")


# --------------------------------------------------------------------------- group


def cmd_group_create(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    try:
        group = client.create_group(args.slug, args.name or args.slug)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"Group '{group.get('slug', args.slug)}' created.")


def cmd_group_set_scopes(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    modules = _split_csv(args.modules) or []

    module_scopes: dict[str, dict[str, list[str]]] = {}
    if args.memory_contexts is not None:
        module_scopes["memory"] = {"contexts": _split_csv(args.memory_contexts) or []}
    if args.docs_categories is not None:
        module_scopes["docs"] = {"categories": _split_csv(args.docs_categories) or []}
    if args.flows_categories is not None:
        module_scopes["flows"] = {"categories": _split_csv(args.flows_categories) or []}

    try:
        client.set_group_scopes(args.slug, modules, module_scopes=module_scopes or None)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"Scopes of '{args.slug}' updated (modules: {', '.join(modules)}).")


def cmd_group_add_member(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    try:
        client.add_group_member(args.slug, args.user)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"User '{args.user}' added to group '{args.slug}'.")
