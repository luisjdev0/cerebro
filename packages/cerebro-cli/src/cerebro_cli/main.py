"""CLI unico del ecosistema cerebro: `cerebro <modulo> <subcomando>` (entry point en
pyproject.toml) mas comandos de nivel ecosistema sin prefijo (ecosistema-cerebro.md
SS11): `cerebro backup`/`restore` y `cerebro token create/revoke` (transversal, SS13).

Como cerebro-mcp, es un cliente delgado de las APIs HTTP via `cerebro_clients` --
ninguna logica de negocio nueva aqui salvo la orquestacion propia del importador de
Markdown (heredada de `cerebro_memory.cli`, ver `memory_commands.py`) y el manejo de
fallo parcial de `cerebro token create/revoke` (ver `shared_commands.py`).

Config: `cerebro_clients.config` -- CEREBRO_MEMORY_URL/CEREBRO_DOCS_URL/CEREBRO_TOKEN,
con fallback KNOWLEDGEOS_API_URL/KNOWLEDGEOS_API_TOKEN para memory (compatibilidad).
"""

from __future__ import annotations

import argparse

from cerebro_cli import docs_commands, memory_commands, shared_commands


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
    p_cat_create.set_defaults(func=docs_commands.cmd_category_create)

    p_cat_list = category_sub.add_parser("list", help="Lista categorias")
    p_cat_list.set_defaults(func=docs_commands.cmd_category_list)

    p_cat_rename = category_sub.add_parser("rename", help="Renombra una categoria (sus documentos no cambian)")
    p_cat_rename.add_argument("slug", help="slug actual")
    p_cat_rename.add_argument("new_slug", help="slug nuevo")
    p_cat_rename.add_argument("--name", default=None, help="tambien actualiza el nombre legible")
    p_cat_rename.add_argument("--description", default=None, help="tambien actualiza la descripcion")
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

    p_docs_stats = docs_sub.add_parser("stats", help="Estadisticas del sistema (categorias/documentos/versiones)")
    p_docs_stats.set_defaults(func=docs_commands.cmd_stats)


def _add_shared_subparsers(sub: argparse._SubParsersAction) -> None:
    p_backup = sub.add_parser("backup", help="pg_dump via docker compose (cubre cerebro_memory y cerebro_docs)")
    p_backup.add_argument("--output", default=None, help="directorio de salida (default: backups/)")
    p_backup.set_defaults(func=shared_commands.cmd_backup)

    p_restore = sub.add_parser("restore", help="Restaura un backup (DESTRUCTIVO)")
    p_restore.add_argument("file", help="archivo .sql generado por 'cerebro backup'")
    p_restore.add_argument("--yes", action="store_true", help="omite la confirmacion interactiva")
    p_restore.set_defaults(func=shared_commands.cmd_restore)

    p_token = sub.add_parser("token", help="Tokens TRANSVERSALES: un secreto, registrado en cerebro-memory y cerebro-docs")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)

    p_token_create = token_sub.add_parser("create", help="Crea (o reintenta) un token transversal")
    p_token_create.add_argument("name")
    p_token_create.add_argument("--scopes", required=True, help="lista separada por comas: read,write,admin")
    p_token_create.add_argument("--contexts", default=None, help="allowed_contexts para cerebro-memory (opcional)")
    p_token_create.add_argument("--categories", default=None, help="allowed_categories para cerebro-docs (opcional)")
    p_token_create.set_defaults(func=shared_commands.cmd_token_create)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoca un token transversal en ambos servicios")
    p_token_revoke.add_argument("name")
    p_token_revoke.set_defaults(func=shared_commands.cmd_token_revoke)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerebro", description="CLI unico del ecosistema cerebro")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_memory_subparser(sub)
    _add_docs_subparser(sub)
    _add_shared_subparsers(sub)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
