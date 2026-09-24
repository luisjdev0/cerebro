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

Password-based login is explicitly out of scope here (future iteration) -- only the
`--token` flow.
"""

from __future__ import annotations

import argparse
import sys

from cerebro_clients import AuthClient, CerebroAPIError, CerebroConnectionError

from cerebro_cli.tokens import save_login_config


def _client(url: str | None = None) -> AuthClient:
    return AuthClient(base_url=url)


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


# --------------------------------------------------------------------------- login


def cmd_login(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    # `--url` is the GATEWAY's base URL (e.g. https://cerebro.example.com), matching
    # every other CEREBRO_*_URL in this project -- the gateway routes /auth/* to
    # cerebro-auth by prefix (gateway/Caddyfile), so the actual connection appends
    # that prefix. The unsuffixed gateway URL is what gets saved to
    # ~/.cerebro/config.json, so cerebro_clients.config's read-fallback can derive
    # each service's own URL (.../memory, .../docs, .../flows, .../auth) from it.
    auth_url = f"{args.url.rstrip('/')}/auth" if args.url else None
    client = client or _client(auth_url)
    try:
        result = client.login(args.token)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            print("Error: token invalido.", file=sys.stderr)
        else:
            print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    # Only persist once login actually succeeded - never write a token that was
    # never validated.
    save_login_config(args.token, args.url)

    modules = result.get("allowed_modules") or []
    print(
        f"Sesion iniciada como '{result.get('name')}' "
        f"(access_level: {result.get('access_level')}; modulos: {', '.join(modules) or 'todos'})."
    )
    print("Credenciales guardadas en ~/.cerebro/config.json.")


# --------------------------------------------------------------------------- user


def cmd_user_create(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    try:
        user = client.create_user(args.name, email=args.email, access_level=args.access_level)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Usuario '{user.get('name', args.name)}' creado "
        f"(id: {user.get('id')}; access_level: {user.get('access_level')})."
    )


def cmd_user_list(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    try:
        users = client.list_users()
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if not users:
        print("(sin usuarios todavia)")
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
        print(f"No se pudo conectar con cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"Grupo '{group.get('slug', args.slug)}' creado.")


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
        print(f"No se pudo conectar con cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"Scopes de '{args.slug}' actualizados (modulos: {', '.join(modules)}).")


def cmd_group_add_member(args: argparse.Namespace, *, client: AuthClient | None = None) -> None:
    client = client or _client()
    try:
        client.add_group_member(args.slug, args.user)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-auth: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"Usuario '{args.user}' agregado al grupo '{args.slug}'.")
