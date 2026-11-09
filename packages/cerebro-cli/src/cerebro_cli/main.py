"""Single CLI for the cerebro ecosystem: `cerebro <module> <subcommand>` (entry point in
pyproject.toml) plus ecosystem-level commands with no prefix (ecosistema-cerebro.md
SS11): `cerebro backup`/`restore`, `cerebro login`, `cerebro user`/`cerebro group`
(identity, via `cerebro-auth`), and `cerebro token create/revoke` (SS13, now a single
call against `cerebro-auth` instead of the old cross-service mechanism).

Like cerebro-mcp, it's a thin client over the HTTP APIs via `cerebro_clients` --
no new business logic here except the Markdown importer's own orchestration
(inherited from `cerebro_memory.cli`, see `memory_commands.py`).

Config: `cerebro_clients.config` -- CEREBRO_MEMORY_URL/CEREBRO_DOCS_URL/CEREBRO_TOKEN,
with fallback to KNOWLEDGEOS_API_URL/KNOWLEDGEOS_API_TOKEN for memory (compatibility).
`main()` also loads `.env.production`/`.env` from the monorepo root before
dispatching any subcommand -- see `dotenv.py`.
"""

from __future__ import annotations

import argparse
import sys

from cerebro_cli import (
    auth_commands,
    docs_commands,
    flow_commands,
    interactive,
    memory_commands,
    shared_commands,
    tokens,
)
from cerebro_cli.dotenv import load_repo_dotenv


def _add_memory_subparser(sub: argparse._SubParsersAction) -> None:
    p_memory = sub.add_parser("memory", help="cerebro-memory subcommands")
    memory_sub = p_memory.add_subparsers(dest="memory_command", required=True)

    p_stats = memory_sub.add_parser("stats", help="System statistics (same as GET /stats)")
    p_stats.set_defaults(func=memory_commands.cmd_stats)

    p_mem_update = memory_sub.add_parser(
        "update", help="Edit a memory: new content creates a version; metadata changes are made in place"
    )
    p_mem_update.add_argument("memory_id")
    p_mem_update.add_argument("--content", default=None, help="new content (creates a new version)")
    p_mem_update.add_argument("--title", default=None)
    p_mem_update.add_argument("--type", dest="type_", default=None, choices=["semantic", "episodic", "procedural", "decision"])
    p_mem_update.add_argument("--importance", type=float, default=None, help="0.0-1.0")
    p_mem_update.add_argument("--confidence", type=float, default=None, help="0.0-1.0")
    p_mem_update.add_argument("--occurred-at", default=None, help="ISO 8601 date or datetime")
    p_mem_update.set_defaults(func=memory_commands.cmd_update)

    p_ctx = memory_sub.add_parser("context", help="Manage contexts")
    ctx_sub = p_ctx.add_subparsers(dest="context_command", required=True)
    p_ctx_list = ctx_sub.add_parser("list", help="List contexts")
    p_ctx_list.set_defaults(func=memory_commands.cmd_context_list)
    p_ctx_create = ctx_sub.add_parser("create", help="Create a context")
    p_ctx_create.add_argument("slug")
    p_ctx_create.add_argument("name")
    p_ctx_create.add_argument("kind", help="e.g. project, domain, person, org")
    p_ctx_create.add_argument("--description", default=None)
    p_ctx_create.set_defaults(func=memory_commands.cmd_context_create)
    p_ctx_update = ctx_sub.add_parser("update", help="Edit a context's name, kind or description (the slug is fixed)")
    p_ctx_update.add_argument("slug")
    p_ctx_update.add_argument("--name", default=None)
    p_ctx_update.add_argument("--kind", default=None)
    p_ctx_update.add_argument("--description", default=None)
    p_ctx_update.set_defaults(func=memory_commands.cmd_context_update)
    p_ctx_delete = ctx_sub.add_parser("delete", help="Delete a context (admin only)")
    p_ctx_delete.add_argument("slug")
    p_ctx_delete.add_argument("--force", action="store_true", help="also delete every memory in it")
    p_ctx_delete.set_defaults(func=memory_commands.cmd_context_delete)

    p_export = memory_sub.add_parser(
        "export-disambiguations", help="Export disambiguation_log to JSONL (Phase 4 dataset)"
    )
    p_export.add_argument("--output", default=None, help="output .jsonl path (default: disambiguations_export.jsonl)")
    p_export.add_argument("--resolved-only", action="store_true", help="only already-resolved disambiguations")
    p_export.set_defaults(func=memory_commands.cmd_export_disambiguations)

    p_import = memory_sub.add_parser(
        "import-markdown", help="Import memories from existing Markdown files (Phase 5, connector 1)"
    )
    p_import.add_argument("path", help=".md file or directory (recursive)")
    p_import.add_argument("--context", required=True, help="slug of the target context")
    p_import.add_argument("--type", dest="type_", default=None, help="force the memory type (default: whatever the parser decides, 'semantic')")
    p_import.add_argument("--dry-run", action="store_true", help="only show what would be imported, write nothing")
    p_import.add_argument("--create-context", action="store_true", help="create the context if it does not exist")
    p_import.add_argument("--context-description", default=None, help="description of the new context (with --create-context)")
    p_import.set_defaults(func=memory_commands.cmd_import_markdown)

    p_token = memory_sub.add_parser("token", help="Manage tokens SCOPED to cerebro-memory (for a cross-service one, use `cerebro token`)")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)

    p_token_create = token_sub.add_parser("create", help="Create a new token, valid for cerebro-memory only (printed ONCE)")
    p_token_create.add_argument("name", help="agent identity, e.g. 'claude-desktop' (unique among active tokens)")
    p_token_create.add_argument("--scopes", required=True, help="comma-separated list: read,write,admin")
    p_token_create.add_argument("--contexts", default=None, help="comma-separated list of slugs; if omitted, the token sees every context")
    p_token_create.set_defaults(func=memory_commands.cmd_token_create)

    p_token_list = token_sub.add_parser("list", help="List cerebro-memory tokens (no hashes or plaintext values)")
    p_token_list.set_defaults(func=memory_commands.cmd_token_list)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoke a cerebro-memory token by name")
    p_token_revoke.add_argument("name", help="name of the token to revoke")
    p_token_revoke.set_defaults(func=memory_commands.cmd_token_revoke)


def _add_docs_subparser(sub: argparse._SubParsersAction) -> None:
    p_docs = sub.add_parser("docs", help="cerebro-docs subcommands")
    docs_sub = p_docs.add_subparsers(dest="docs_command", required=True)

    p_category = docs_sub.add_parser("category", help="Manage categories")
    category_sub = p_category.add_subparsers(dest="category_command", required=True)

    p_cat_create = category_sub.add_parser("create", help="Create a new category")
    p_cat_create.add_argument("slug")
    p_cat_create.add_argument("--name", default=None, help="human-readable name (default: the slug)")
    p_cat_create.add_argument("--description", default=None)
    p_cat_create.add_argument("--hidden", action="store_true", help="does not show up in list/search without the exact slug")
    p_cat_create.add_argument(
        "--locked",
        action="store_true",
        help="with --hidden: hide it FOREVER (it can never be revealed later, not even by an admin)",
    )
    p_cat_create.set_defaults(func=docs_commands.cmd_category_create)

    p_cat_list = category_sub.add_parser("list", help="List categories")
    p_cat_list.set_defaults(func=docs_commands.cmd_category_list)

    p_cat_rename = category_sub.add_parser(
        "rename", help="Rename/edit a category (current slug == new slug to only touch name/description/hidden)"
    )
    p_cat_rename.add_argument("slug", help="current slug")
    p_cat_rename.add_argument("new_slug", help="new slug (repeat the current one if you do not want to change it)")
    p_cat_rename.add_argument("--name", default=None, help="also update the human-readable name")
    p_cat_rename.add_argument("--description", default=None, help="also update the description")
    p_cat_visibility = p_cat_rename.add_mutually_exclusive_group()
    p_cat_visibility.add_argument("--hidden", action="store_true", help="hide the category")
    p_cat_visibility.add_argument(
        "--visible", action="store_true", help="reveal the category (fails if it is 'locked')"
    )
    p_cat_rename.set_defaults(func=docs_commands.cmd_category_rename)

    p_cat_delete = category_sub.add_parser("delete", help="Delete a category (409 if it has documents, unless --force)")
    p_cat_delete.add_argument("slug")
    p_cat_delete.add_argument("--force", action="store_true", help="cascade-delete its documents and version history")
    p_cat_delete.set_defaults(func=docs_commands.cmd_category_delete)

    p_save = docs_sub.add_parser("save", help="Save a new, complete Markdown document")
    p_save.add_argument("category")
    p_save.add_argument("title")
    p_save.add_argument("--content-file", default=None, help="path to a .md file (read from stdin if omitted)")
    p_save.add_argument("--slug", default=None, help="document slug (default: derived from the title)")
    p_save.set_defaults(func=docs_commands.cmd_save)

    p_get = docs_sub.add_parser("get", help="Read a document by its exact path")
    p_get.add_argument("category")
    p_get.add_argument("slug")
    p_get.set_defaults(func=docs_commands.cmd_get)

    p_list = docs_sub.add_parser("list", help="List documents (most recent first)")
    p_list.add_argument("--category", default=None)
    p_list.add_argument("--archived", action="store_true", help="list archived instead of active ones")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.set_defaults(func=docs_commands.cmd_list)

    p_search = docs_sub.add_parser("search", help="Search documents by text (simple full-text)")
    p_search.add_argument("query")
    p_search.add_argument("--category", default=None)
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--offset", type=int, default=0)
    p_search.set_defaults(func=docs_commands.cmd_search)

    p_update = docs_sub.add_parser("update", help="Fully replace a document (including moving it to another category)")
    p_update.add_argument("document_id")
    p_update.add_argument("title")
    p_update.add_argument("category")
    p_update.add_argument("--content-file", default=None, help="path to a .md file (read from stdin if omitted)")
    p_update.add_argument("--slug", default=None, help="new slug (default: keep the current one)")
    p_update.set_defaults(func=docs_commands.cmd_update)

    p_patch = docs_sub.add_parser("patch-section", help="Partial patch by heading (replace/append/insert_after/insert_before/delete)")
    p_patch.add_argument("document_id")
    p_patch.add_argument("heading")
    p_patch.add_argument("operation", choices=["replace", "append", "insert_after", "insert_before", "delete"])
    p_patch.add_argument("--body", default=None, help="patch content (alternative to --body-file/stdin)")
    p_patch.add_argument("--body-file", default=None, help="path to a file with the patch content")
    p_patch.add_argument("--create-if-missing", action="store_true")
    p_patch.add_argument("--new-heading-level", type=int, default=2)
    p_patch.set_defaults(func=docs_commands.cmd_patch_section)

    p_delete = docs_sub.add_parser("delete", help="Delete a document (irreversible)")
    p_delete.add_argument("document_id")
    p_delete.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    p_delete.set_defaults(func=docs_commands.cmd_delete)

    p_archive = docs_sub.add_parser("archive", help="Archive a document (soft delete, reversible with unarchive)")
    p_archive.add_argument("document_id")
    p_archive.set_defaults(func=docs_commands.cmd_archive)

    p_unarchive = docs_sub.add_parser("unarchive", help="Undo an archive")
    p_unarchive.add_argument("document_id")
    p_unarchive.set_defaults(func=docs_commands.cmd_unarchive)

    p_history = docs_sub.add_parser("history", help="List a document's previous version history")
    p_history.add_argument("document_id")
    p_history.set_defaults(func=docs_commands.cmd_history)

    p_docs_import = docs_sub.add_parser(
        "import-markdown", help="Import complete (undistilled) documents from existing Markdown files"
    )
    p_docs_import.add_argument("path", help=".md file or directory (recursive)")
    p_docs_import.add_argument("--category", required=True, help="slug of the target category (must exist)")
    p_docs_import.add_argument("--dry-run", action="store_true", help="only show what would be imported, write nothing")
    p_docs_import.add_argument(
        "--update", action="store_true", help="if (category, slug) already exists, update it instead of skipping it"
    )
    p_docs_import.set_defaults(func=docs_commands.cmd_import_markdown)

    p_docs_stats = docs_sub.add_parser("stats", help="System statistics (categories/documents/versions)")
    p_docs_stats.set_defaults(func=docs_commands.cmd_stats)


def _add_flows_subparser(sub: argparse._SubParsersAction) -> None:
    p_flow = sub.add_parser("flow", help="cerebro-flows subcommands (CRUD of definitions -- running a flow is done by a model via MCP)")
    flow_sub = p_flow.add_subparsers(dest="flow_command", required=True)

    p_category = flow_sub.add_parser("category", help="Manage flow categories")
    category_sub = p_category.add_subparsers(dest="category_command", required=True)

    p_cat_create = category_sub.add_parser("create", help="Create a new category")
    p_cat_create.add_argument("slug")
    p_cat_create.add_argument("code", help="short uppercase prefix for its flows' ids, e.g. INC")
    p_cat_create.add_argument("--name", default=None, help="human-readable name (default: the slug)")
    p_cat_create.add_argument("--description", default=None)
    p_cat_create.set_defaults(func=flow_commands.cmd_category_create)

    p_cat_list = category_sub.add_parser("list", help="List categories")
    p_cat_list.set_defaults(func=flow_commands.cmd_category_list)

    p_validate = flow_sub.add_parser("validate", help="Validate a flow YAML without saving it")
    p_validate.add_argument("--yaml-file", required=True, help="path to the flow's YAML file")
    p_validate.set_defaults(func=flow_commands.cmd_validate)

    p_save = flow_sub.add_parser("save", help="Save a new flow")
    p_save.add_argument("category")
    p_save.add_argument("--yaml-file", required=True, help="path to the flow's YAML file")
    p_save.add_argument("--code", default=None, help="explicit sequential id (default: auto-generated)")
    p_save.set_defaults(func=flow_commands.cmd_save)

    p_get = flow_sub.add_parser("get", help="Read a flow's complete YAML by its code")
    p_get.add_argument("code")
    p_get.set_defaults(func=flow_commands.cmd_get)

    p_diagram = flow_sub.add_parser("diagram", help="Print a flow as a Mermaid flowchart")
    p_diagram.add_argument("code")
    p_diagram.add_argument("--version", type=int, default=None, help="saved version number (default: current)")
    p_diagram.set_defaults(func=flow_commands.cmd_diagram)

    p_list = flow_sub.add_parser("list", help="List flow definitions")
    p_list.add_argument("--category", default=None)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.set_defaults(func=flow_commands.cmd_list)

    p_update = flow_sub.add_parser("update", help="Replace a flow's YAML (new version)")
    p_update.add_argument("code")
    p_update.add_argument("--yaml-file", required=True, help="path to the flow's YAML file")
    p_update.set_defaults(func=flow_commands.cmd_update)

    p_delete = flow_sub.add_parser("delete", help="Delete a flow (irreversible)")
    p_delete.add_argument("code")
    p_delete.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    p_delete.set_defaults(func=flow_commands.cmd_delete)

    p_flow_stats = flow_sub.add_parser("stats", help="System statistics (categories/flows/runs)")
    p_flow_stats.set_defaults(func=flow_commands.cmd_stats)


def _add_shared_subparsers(sub: argparse._SubParsersAction) -> None:
    p_backup = sub.add_parser("backup", help="Download a full pg_dump through cerebro-auth (POST /backup, admin-only)")
    p_backup.add_argument("--output", default=None, help="output directory (default: ~/cerebro-backups/)")
    p_backup.set_defaults(func=shared_commands.cmd_backup)

    p_restore = sub.add_parser("restore", help="Restore a backup (DESTRUCTIVE)")
    p_restore.add_argument("file", help=".sql file produced by 'cerebro backup'")
    p_restore.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    p_restore.set_defaults(func=shared_commands.cmd_restore)

    p_token = sub.add_parser("token", help="Manage ecosystem tokens (cerebro-auth)")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)

    p_token_create = token_sub.add_parser("create", help="Create a new token in cerebro-auth")
    p_token_create.add_argument("name", help="token identity, e.g. 'claude-desktop' (unique among active tokens)")
    p_token_create.add_argument("--scopes", required=True, help="comma-separated list: read,write")
    p_token_create.add_argument("--modules", default=None, help="comma-separated list: memory,docs,flows")
    p_token_create.add_argument(
        "--user", default=None, help="user that owns the token (do not combine with --access-level)"
    )
    p_token_create.add_argument(
        "--access-level",
        choices=["user", "owner", "admin"],
        default=None,
        help="required only if --user is omitted (service token, no owning user)",
    )
    p_token_create.add_argument("--memory-contexts", default=None, help="comma-separated list of slugs (optional)")
    p_token_create.add_argument("--docs-categories", default=None, help="comma-separated list of slugs (optional)")
    p_token_create.add_argument("--flows-categories", default=None, help="comma-separated list of slugs (optional)")
    p_token_create.set_defaults(func=shared_commands.cmd_token_create)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoke a token in cerebro-auth")
    p_token_revoke.add_argument("name")
    p_token_revoke.set_defaults(func=shared_commands.cmd_token_revoke)


def _add_auth_subparsers(sub: argparse._SubParsersAction) -> None:
    p_login = sub.add_parser(
        "login", help="Log in (username and password, or token) and save the credentials locally"
    )
    p_login.add_argument("--user", default=None, help="username (the password is prompted)")
    p_login.add_argument(
        "--password",
        nargs="?",
        const=auth_commands.ASK,
        default=None,
        help="password. Without a value it is asked with a prompt (recommended). "
        "--password=VALUE is INSECURE: it stays in your shell history and in `ps`",
    )
    p_login.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )
    p_login.add_argument("--token", default=None, help="cerebro-auth token (alternative to --user)")
    p_login.add_argument("--url", default=None, help="gateway URL (default: the one resolved from the environment)")
    p_login.set_defaults(func=auth_commands.cmd_login)

    p_logout = sub.add_parser("logout", help="Log out and delete the local credentials")
    p_logout.set_defaults(func=auth_commands.cmd_logout)

    p_user = sub.add_parser("user", help="Manage users (cerebro-auth, requires admin access_level)")
    user_sub = p_user.add_subparsers(dest="user_command", required=True)

    p_user_create = user_sub.add_parser("create", help="Create a new user")
    p_user_create.add_argument("name")
    p_user_create.add_argument("--email", default=None)
    p_user_create.add_argument("--access-level", choices=["user", "owner", "admin"], default="user")
    p_user_create.add_argument(
        "--password",
        nargs="?",
        const=auth_commands.ASK,
        default=None,
        help="set a password (username/password login). Without a value it is asked with a prompt; "
        "--password=VALUE is INSECURE (shell history, `ps`)",
    )
    p_user_create.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    p_user_create.set_defaults(func=auth_commands.cmd_user_create)

    p_user_pw = user_sub.add_parser(
        "set-password",
        help="Change a user's password (admin: anyone's; user: their own, with the current one)",
    )
    p_user_pw.add_argument("name")
    p_user_pw.add_argument(
        "--password",
        nargs="?",
        const=auth_commands.ASK,
        default=None,
        help="new password. Without a value it is asked with a prompt; --password=VALUE is INSECURE",
    )
    p_user_pw.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    p_user_pw.add_argument(
        "--current-password",
        nargs="?",
        const=auth_commands.ASK,
        default=None,
        help="current password (required when changing your own). Without a value it is asked with a prompt",
    )
    p_user_pw.set_defaults(func=auth_commands.cmd_user_set_password)

    p_user_list = user_sub.add_parser("list", help="List users")
    p_user_list.set_defaults(func=auth_commands.cmd_user_list)

    p_group = sub.add_parser("group", help="Manage groups (cerebro-auth, requires admin access_level)")
    group_sub = p_group.add_subparsers(dest="group_command", required=True)

    p_group_create = group_sub.add_parser("create", help="Create a new group")
    p_group_create.add_argument("slug")
    p_group_create.add_argument("--name", default=None, help="human-readable name (default: the slug)")
    p_group_create.set_defaults(func=auth_commands.cmd_group_create)

    p_group_scopes = group_sub.add_parser("set-scopes", help="Set the modules/scopes allowed for a group")
    p_group_scopes.add_argument("slug")
    p_group_scopes.add_argument("--modules", required=True, help="comma-separated list: memory,docs,flows")
    p_group_scopes.add_argument("--memory-contexts", default=None, help="comma-separated list of slugs (optional)")
    p_group_scopes.add_argument("--docs-categories", default=None, help="comma-separated list of slugs (optional)")
    p_group_scopes.add_argument("--flows-categories", default=None, help="comma-separated list of slugs (optional)")
    p_group_scopes.set_defaults(func=auth_commands.cmd_group_set_scopes)

    p_group_member = group_sub.add_parser("add-member", help="Add a user to a group")
    p_group_member.add_argument("slug")
    p_group_member.add_argument("user")
    p_group_member.set_defaults(func=auth_commands.cmd_group_add_member)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerebro", description="Single CLI for the cerebro ecosystem")
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="never ask questions (for scripts); same as CEREBRO_NO_INTERACTIVE=1",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_memory_subparser(sub)
    _add_docs_subparser(sub)
    _add_flows_subparser(sub)
    _add_shared_subparsers(sub)
    _add_auth_subparsers(sub)

    return parser


def _needs_session_notice(command: str | None) -> None:
    """A saved session that already expired is reported up front instead of as an
    opaque 401 from whichever module the command happens to call."""
    if command in interactive.SESSION_FREE or tokens.session_state() != "expired":
        return
    print("Your session has expired. Log in again with `cerebro login`.", file=sys.stderr)
    sys.exit(1)


def _login_incomplete(path: list[str], rest: list[str]) -> bool:
    return path == ["login"] and not any(t in ("--user", "--token") or t.startswith(("--user=", "--token=")) for t in rest)


def _interactive_argv(parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """Turn an incomplete command line into a complete one by asking the user."""
    leaf, path, rest = interactive.descend(parser, argv)

    # Without a valid session the only thing on offer is logging in; afterwards the
    # original request (or the full menu) carries on.
    state = tokens.session_state()
    if state != "valid" and not (path and path[0] in interactive.SESSION_FREE):
        interactive.banner(state)
        login = parser.parse_args(interactive.login_argv())
        login.func(login)
        if tokens.session_state() != "valid":
            sys.exit(1)

    if _login_incomplete(path, rest):
        return interactive.login_argv()

    if interactive._subparsers_action(leaf) is not None:
        chosen = interactive.menu(leaf, path, has_session=True, root=not path)
        if chosen is None:
            sys.exit(0)
        leaf, path, _ = interactive.descend(parser, chosen)
        rest = []
        if path == ["login"]:
            return interactive.login_argv()
    return path + interactive.complete(leaf, path, rest)


def main(argv: list[str] | None = None) -> None:
    # BEFORE building any client (subcommands create them via
    # cerebro_clients, which reads os.environ on every call): load .env.production/.env
    # from the monorepo root without overwriting anything already present -- see dotenv.py for
    # why (replaces the old .venv\Scripts\cerebro.cmd wrapper that did this by
    # hand and that the entry point installed by pip now shadows on the PATH).
    load_repo_dotenv()
    argv = list(sys.argv[1:] if argv is None else argv)
    interactive_ok = interactive.is_interactive(argv)
    argv = [a for a in argv if a != interactive.NO_FLAG]  # accepted anywhere on the line
    parser = build_parser()

    if interactive_ok and "-h" not in argv and "--help" not in argv:
        leaf, path, rest = interactive.descend(parser, argv)
        needs_menu = not path or interactive._subparsers_action(leaf) is not None
        needs_login = tokens.session_state() != "valid" and not (path and path[0] in interactive.SESSION_FREE)
        if needs_menu or needs_login or _login_incomplete(path, rest) or _missing(leaf, rest):
            try:
                argv = _interactive_argv(parser, argv)
            except KeyboardInterrupt:
                print()
                sys.exit(130)

    args = parser.parse_args(argv)
    _needs_session_notice(args.command)
    args.func(args)


def _missing(leaf: argparse.ArgumentParser, rest: list[str]) -> bool:
    """True when `rest` leaves a required argument of `leaf` unset."""
    if interactive._subparsers_action(leaf) is not None:
        return False
    with interactive._relaxed(leaf):
        try:
            ns, _ = leaf.parse_known_args(rest)
        except SystemExit:
            return False
    for a in leaf._actions:
        required = a.required or (not a.option_strings and a.nargs is None)
        if required and not isinstance(a, argparse._HelpAction) and getattr(ns, a.dest, None) in (None, []):
            return True
    return False


if __name__ == "__main__":
    main()
