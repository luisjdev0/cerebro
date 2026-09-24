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

from cerebro_cli import auth_commands, docs_commands, flow_commands, memory_commands, shared_commands
from cerebro_cli.dotenv import load_repo_dotenv


def _add_memory_subparser(sub: argparse._SubParsersAction) -> None:
    p_memory = sub.add_parser("memory", help="Subcomandos de cerebro-memory")
    memory_sub = p_memory.add_subparsers(dest="memory_command", required=True)

    p_stats = memory_sub.add_parser("stats", help="Estadisticas del sistema (igual que GET /stats)")
    p_stats.set_defaults(func=memory_commands.cmd_stats)

    p_export = memory_sub.add_parser(
        "export-disambiguations", help="Exporta disambiguation_log a JSONL (dataset de la Fase 4)"
    )
    p_export.add_argument("--output", default=None, help="ruta del .jsonl de salida (default: disambiguations_export.jsonl)")
    p_export.add_argument("--resolved-only", action="store_true", help="solo desambiguaciones ya resueltas")
    p_export.set_defaults(func=memory_commands.cmd_export_disambiguations)

    p_import = memory_sub.add_parser(
        "import-markdown", help="Importa memorias desde archivos Markdown existentes (Fase 5, conector 1)"
    )
    p_import.add_argument("path", help="archivo .md o directorio (recursivo)")
    p_import.add_argument("--context", required=True, help="slug del contexto destino")
    p_import.add_argument("--type", dest="type_", default=None, help="fuerza el tipo de memoria (default: el que decida el parser, 'semantic')")
    p_import.add_argument("--dry-run", action="store_true", help="solo muestra que se importaria, sin escribir nada")
    p_import.add_argument("--create-context", action="store_true", help="crea el contexto si no existe")
    p_import.add_argument("--context-description", default=None, help="descripcion del contexto nuevo (con --create-context)")
    p_import.set_defaults(func=memory_commands.cmd_import_markdown)

    p_token = memory_sub.add_parser("token", help="Gestion de tokens ESCOPADOS a cerebro-memory (para uno transversal, usa `cerebro token`)")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)

    p_token_create = token_sub.add_parser("create", help="Crea un token nuevo, solo valido para cerebro-memory (lo imprime UNA vez)")
    p_token_create.add_argument("name", help="identidad del agente, ej. 'claude-desktop' (unica entre tokens activos)")
    p_token_create.add_argument("--scopes", required=True, help="lista separada por comas: read,write,admin")
    p_token_create.add_argument("--contexts", default=None, help="lista de slugs separada por comas; si se omite, el token ve todos los contextos")
    p_token_create.set_defaults(func=memory_commands.cmd_token_create)

    p_token_list = token_sub.add_parser("list", help="Lista tokens de cerebro-memory (sin hashes ni valores en claro)")
    p_token_list.set_defaults(func=memory_commands.cmd_token_list)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoca un token de cerebro-memory por nombre")
    p_token_revoke.add_argument("name", help="nombre del token a revocar")
    p_token_revoke.set_defaults(func=memory_commands.cmd_token_revoke)


def _add_docs_subparser(sub: argparse._SubParsersAction) -> None:
    p_docs = sub.add_parser("docs", help="Subcomandos de cerebro-docs")
    docs_sub = p_docs.add_subparsers(dest="docs_command", required=True)

    p_category = docs_sub.add_parser("category", help="Gestion de categorias")
    category_sub = p_category.add_subparsers(dest="category_command", required=True)

    p_cat_create = category_sub.add_parser("create", help="Crea una categoria nueva")
    p_cat_create.add_argument("slug")
    p_cat_create.add_argument("--name", default=None, help="nombre legible (default: el slug)")
    p_cat_create.add_argument("--description", default=None)
    p_cat_create.add_argument("--hidden", action="store_true", help="no aparece en list/search sin slug exacto")
    p_cat_create.add_argument(
        "--locked",
        action="store_true",
        help="con --hidden: la oculta para SIEMPRE (nunca se podra revelar despues, ni por admin)",
    )
    p_cat_create.set_defaults(func=docs_commands.cmd_category_create)

    p_cat_list = category_sub.add_parser("list", help="Lista categorias")
    p_cat_list.set_defaults(func=docs_commands.cmd_category_list)

    p_cat_rename = category_sub.add_parser(
        "rename", help="Renombra/edita una categoria (slug actual == slug nuevo para solo tocar name/description/hidden)"
    )
    p_cat_rename.add_argument("slug", help="slug actual")
    p_cat_rename.add_argument("new_slug", help="slug nuevo (repite el actual si no quieres cambiarlo)")
    p_cat_rename.add_argument("--name", default=None, help="tambien actualiza el nombre legible")
    p_cat_rename.add_argument("--description", default=None, help="tambien actualiza la descripcion")
    p_cat_visibility = p_cat_rename.add_mutually_exclusive_group()
    p_cat_visibility.add_argument("--hidden", action="store_true", help="oculta la categoria")
    p_cat_visibility.add_argument(
        "--visible", action="store_true", help="revela la categoria (falla si esta 'locked')"
    )
    p_cat_rename.set_defaults(func=docs_commands.cmd_category_rename)

    p_cat_delete = category_sub.add_parser("delete", help="Borra una categoria (409 si tiene documentos, salvo --force)")
    p_cat_delete.add_argument("slug")
    p_cat_delete.add_argument("--force", action="store_true", help="borra en cascada sus documentos y version history")
    p_cat_delete.set_defaults(func=docs_commands.cmd_category_delete)

    p_save = docs_sub.add_parser("save", help="Guarda un documento Markdown nuevo, completo")
    p_save.add_argument("category")
    p_save.add_argument("title")
    p_save.add_argument("--content-file", default=None, help="ruta a un archivo .md (si se omite, lee de stdin)")
    p_save.add_argument("--slug", default=None, help="slug del documento (default: derivado del titulo)")
    p_save.set_defaults(func=docs_commands.cmd_save)

    p_get = docs_sub.add_parser("get", help="Lee un documento por su ruta exacta")
    p_get.add_argument("category")
    p_get.add_argument("slug")
    p_get.set_defaults(func=docs_commands.cmd_get)

    p_list = docs_sub.add_parser("list", help="Lista documentos (mas recientes primero)")
    p_list.add_argument("--category", default=None)
    p_list.add_argument("--archived", action="store_true", help="lista archivados en vez de activos")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.set_defaults(func=docs_commands.cmd_list)

    p_search = docs_sub.add_parser("search", help="Busca documentos por texto (full-text simple)")
    p_search.add_argument("query")
    p_search.add_argument("--category", default=None)
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--offset", type=int, default=0)
    p_search.set_defaults(func=docs_commands.cmd_search)

    p_update = docs_sub.add_parser("update", help="Reemplazo completo de un documento (incluye moverlo de categoria)")
    p_update.add_argument("document_id")
    p_update.add_argument("title")
    p_update.add_argument("category")
    p_update.add_argument("--content-file", default=None, help="ruta a un archivo .md (si se omite, lee de stdin)")
    p_update.add_argument("--slug", default=None, help="nuevo slug (default: conserva el actual)")
    p_update.set_defaults(func=docs_commands.cmd_update)

    p_patch = docs_sub.add_parser("patch-section", help="Parche parcial por heading (replace/append/insert_after/insert_before/delete)")
    p_patch.add_argument("document_id")
    p_patch.add_argument("heading")
    p_patch.add_argument("operation", choices=["replace", "append", "insert_after", "insert_before", "delete"])
    p_patch.add_argument("--body", default=None, help="contenido del parche (alternativa a --body-file/stdin)")
    p_patch.add_argument("--body-file", default=None, help="ruta a un archivo con el contenido del parche")
    p_patch.add_argument("--create-if-missing", action="store_true")
    p_patch.add_argument("--new-heading-level", type=int, default=2)
    p_patch.set_defaults(func=docs_commands.cmd_patch_section)

    p_delete = docs_sub.add_parser("delete", help="Borra un documento (irreversible)")
    p_delete.add_argument("document_id")
    p_delete.add_argument("--yes", action="store_true", help="omite la confirmacion interactiva")
    p_delete.set_defaults(func=docs_commands.cmd_delete)

    p_archive = docs_sub.add_parser("archive", help="Archiva un documento (soft-delete, reversible con unarchive)")
    p_archive.add_argument("document_id")
    p_archive.set_defaults(func=docs_commands.cmd_archive)

    p_unarchive = docs_sub.add_parser("unarchive", help="Revierte un archive")
    p_unarchive.add_argument("document_id")
    p_unarchive.set_defaults(func=docs_commands.cmd_unarchive)

    p_history = docs_sub.add_parser("history", help="Lista el historial de versiones anteriores de un documento")
    p_history.add_argument("document_id")
    p_history.set_defaults(func=docs_commands.cmd_history)

    p_docs_import = docs_sub.add_parser(
        "import-markdown", help="Importa documentos completos (sin destilar) desde archivos Markdown existentes"
    )
    p_docs_import.add_argument("path", help="archivo .md o directorio (recursivo)")
    p_docs_import.add_argument("--category", required=True, help="slug de la categoria destino (debe existir)")
    p_docs_import.add_argument("--dry-run", action="store_true", help="solo muestra que se importaria, sin escribir nada")
    p_docs_import.add_argument(
        "--update", action="store_true", help="si el (categoria, slug) ya existe, actualizalo en vez de omitirlo"
    )
    p_docs_import.set_defaults(func=docs_commands.cmd_import_markdown)

    p_docs_stats = docs_sub.add_parser("stats", help="Estadisticas del sistema (categorias/documentos/versiones)")
    p_docs_stats.set_defaults(func=docs_commands.cmd_stats)


def _add_flows_subparser(sub: argparse._SubParsersAction) -> None:
    p_flow = sub.add_parser("flow", help="Subcomandos de cerebro-flows (CRUD de definiciones -- ejecutar un flujo lo hace un modelo via MCP)")
    flow_sub = p_flow.add_subparsers(dest="flow_command", required=True)

    p_category = flow_sub.add_parser("category", help="Gestion de categorias de flujo")
    category_sub = p_category.add_subparsers(dest="category_command", required=True)

    p_cat_create = category_sub.add_parser("create", help="Crea una categoria nueva")
    p_cat_create.add_argument("slug")
    p_cat_create.add_argument("code", help="prefijo corto en mayusculas para los ids de sus flujos, ej. INC")
    p_cat_create.add_argument("--name", default=None, help="nombre legible (default: el slug)")
    p_cat_create.add_argument("--description", default=None)
    p_cat_create.set_defaults(func=flow_commands.cmd_category_create)

    p_cat_list = category_sub.add_parser("list", help="Lista categorias")
    p_cat_list.set_defaults(func=flow_commands.cmd_category_list)

    p_validate = flow_sub.add_parser("validate", help="Valida un YAML de flujo sin guardarlo")
    p_validate.add_argument("--yaml-file", required=True, help="ruta al archivo YAML del flujo")
    p_validate.set_defaults(func=flow_commands.cmd_validate)

    p_save = flow_sub.add_parser("save", help="Guarda un flujo nuevo")
    p_save.add_argument("category")
    p_save.add_argument("--yaml-file", required=True, help="ruta al archivo YAML del flujo")
    p_save.add_argument("--code", default=None, help="id correlativo explicito (default: autogenerado)")
    p_save.set_defaults(func=flow_commands.cmd_save)

    p_get = flow_sub.add_parser("get", help="Lee el YAML completo de un flujo por su code")
    p_get.add_argument("code")
    p_get.set_defaults(func=flow_commands.cmd_get)

    p_list = flow_sub.add_parser("list", help="Lista definiciones de flujo")
    p_list.add_argument("--category", default=None)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.set_defaults(func=flow_commands.cmd_list)

    p_update = flow_sub.add_parser("update", help="Reemplaza el YAML de un flujo (nueva version)")
    p_update.add_argument("code")
    p_update.add_argument("--yaml-file", required=True, help="ruta al archivo YAML del flujo")
    p_update.set_defaults(func=flow_commands.cmd_update)

    p_delete = flow_sub.add_parser("delete", help="Borra un flujo (irreversible)")
    p_delete.add_argument("code")
    p_delete.add_argument("--yes", action="store_true", help="omite la confirmacion interactiva")
    p_delete.set_defaults(func=flow_commands.cmd_delete)

    p_flow_stats = flow_sub.add_parser("stats", help="Estadisticas del sistema (categorias/flujos/runs)")
    p_flow_stats.set_defaults(func=flow_commands.cmd_stats)


def _add_shared_subparsers(sub: argparse._SubParsersAction) -> None:
    p_backup = sub.add_parser("backup", help="pg_dump via docker compose (cubre cerebro_memory y cerebro_docs)")
    p_backup.add_argument("--output", default=None, help="directorio de salida (default: backups/)")
    p_backup.set_defaults(func=shared_commands.cmd_backup)

    p_restore = sub.add_parser("restore", help="Restaura un backup (DESTRUCTIVO)")
    p_restore.add_argument("file", help="archivo .sql generado por 'cerebro backup'")
    p_restore.add_argument("--yes", action="store_true", help="omite la confirmacion interactiva")
    p_restore.set_defaults(func=shared_commands.cmd_restore)

    p_token = sub.add_parser("token", help="Gestion de tokens del ecosistema (cerebro-auth)")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)

    p_token_create = token_sub.add_parser("create", help="Crea un token nuevo en cerebro-auth")
    p_token_create.add_argument("name", help="identidad del token, ej. 'claude-desktop' (unica entre tokens activos)")
    p_token_create.add_argument("--scopes", required=True, help="lista separada por comas: read,write")
    p_token_create.add_argument("--modules", default=None, help="lista separada por comas: memory,docs,flows")
    p_token_create.add_argument(
        "--user", default=None, help="usuario dueño del token (no combinar con --access-level)"
    )
    p_token_create.add_argument(
        "--access-level",
        choices=["user", "owner", "admin"],
        default=None,
        help="requerido solo si se omite --user (token de servicio, sin usuario dueño)",
    )
    p_token_create.add_argument("--memory-contexts", default=None, help="lista de slugs separada por comas (opcional)")
    p_token_create.add_argument("--docs-categories", default=None, help="lista de slugs separada por comas (opcional)")
    p_token_create.add_argument("--flows-categories", default=None, help="lista de slugs separada por comas (opcional)")
    p_token_create.set_defaults(func=shared_commands.cmd_token_create)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoca un token en cerebro-auth")
    p_token_revoke.add_argument("name")
    p_token_revoke.set_defaults(func=shared_commands.cmd_token_revoke)


def _add_auth_subparsers(sub: argparse._SubParsersAction) -> None:
    p_login = sub.add_parser(
        "login", help="Inicia sesion con un token de cerebro-auth y guarda las credenciales localmente"
    )
    p_login.add_argument("--token", required=True, help="token de cerebro-auth")
    p_login.add_argument("--url", default=None, help="URL de cerebro-auth (default: la resuelta por entorno)")
    p_login.set_defaults(func=auth_commands.cmd_login)

    p_user = sub.add_parser("user", help="Gestion de usuarios (cerebro-auth, requiere access_level admin)")
    user_sub = p_user.add_subparsers(dest="user_command", required=True)

    p_user_create = user_sub.add_parser("create", help="Crea un usuario nuevo")
    p_user_create.add_argument("name")
    p_user_create.add_argument("--email", default=None)
    p_user_create.add_argument("--access-level", choices=["user", "owner", "admin"], default="user")
    p_user_create.set_defaults(func=auth_commands.cmd_user_create)

    p_user_list = user_sub.add_parser("list", help="Lista usuarios")
    p_user_list.set_defaults(func=auth_commands.cmd_user_list)

    p_group = sub.add_parser("group", help="Gestion de grupos (cerebro-auth, requiere access_level admin)")
    group_sub = p_group.add_subparsers(dest="group_command", required=True)

    p_group_create = group_sub.add_parser("create", help="Crea un grupo nuevo")
    p_group_create.add_argument("slug")
    p_group_create.add_argument("--name", default=None, help="nombre legible (default: el slug)")
    p_group_create.set_defaults(func=auth_commands.cmd_group_create)

    p_group_scopes = group_sub.add_parser("set-scopes", help="Define los modulos/alcances permitidos para un grupo")
    p_group_scopes.add_argument("slug")
    p_group_scopes.add_argument("--modules", required=True, help="lista separada por comas: memory,docs,flows")
    p_group_scopes.add_argument("--memory-contexts", default=None, help="lista de slugs separada por comas (opcional)")
    p_group_scopes.add_argument("--docs-categories", default=None, help="lista de slugs separada por comas (opcional)")
    p_group_scopes.add_argument("--flows-categories", default=None, help="lista de slugs separada por comas (opcional)")
    p_group_scopes.set_defaults(func=auth_commands.cmd_group_set_scopes)

    p_group_member = group_sub.add_parser("add-member", help="Agrega un usuario a un grupo")
    p_group_member.add_argument("slug")
    p_group_member.add_argument("user")
    p_group_member.set_defaults(func=auth_commands.cmd_group_add_member)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerebro", description="CLI unico del ecosistema cerebro")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_memory_subparser(sub)
    _add_docs_subparser(sub)
    _add_flows_subparser(sub)
    _add_shared_subparsers(sub)
    _add_auth_subparsers(sub)

    return parser


def main(argv: list[str] | None = None) -> None:
    # BEFORE building any client (subcommands create them via
    # cerebro_clients, which reads os.environ on every call): load .env.production/.env
    # from the monorepo root without overwriting anything already present -- see dotenv.py for
    # why (replaces the old .venv\Scripts\cerebro.cmd wrapper that did this by
    # hand and that the entry point installed by pip now shadows on the PATH).
    load_repo_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
