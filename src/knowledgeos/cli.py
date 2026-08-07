"""CLI de KnowledgeOS: `knowledgeos <subcomando>` (entry point en pyproject.toml).

Como el servidor MCP (`knowledgeos.mcp_server`), es un cliente delgado de la API HTTP
-- ninguna lógica de negocio nueva aquí salvo la orquestación propia del importador de
Markdown (parsing puro vive en `knowledgeos.markdown_importer`). La única excepción es
`backup`/`restore`, que hablan directo con `docker compose` (Postgres no expone su
puerto fuera de localhost, y un dump/restore no tiene sentido como llamada HTTP).

Config: lee `.env` (vía `knowledgeos.config.get_settings`, igual que la API) para
`API_TOKEN`, y por defecto asume la API en `http://localhost:<APP_PORT>`. Se puede
sobreescribir con `KNOWLEDGEOS_API_URL`/`KNOWLEDGEOS_API_TOKEN` (mismas variables que
el servidor MCP, ver README) si la API corre en otra máquina.

Subcomandos:
    export-disambiguations   dataset de disambiguation_log -> JSONL (Fase 4)
    stats                    igual que GET /stats, formateado para consola
    import-markdown          importador de Markdown (Fase 5, conector 1)
    backup / restore         pg_dump / restore vía docker compose
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from knowledgeos.config import get_settings
from knowledgeos.db import REPO_ROOT
from knowledgeos.markdown_importer import ParsedMemory, iter_markdown_files, parse_markdown_file

# RRF score de un hit que gana el rank #1 tanto en la busqueda vectorial como en full
# text es ~2/(60+1) ~= 0.033 (ver retrieval.reciprocal_rank_fusion, k=60 default). Un
# duplicado casi exacto del mismo contenido debería aterrizar ahí; ponemos el umbral
# algo por debajo para tolerar variacion menor sin abrir la puerta a falsos positivos
# (el requisito de titulo identico, exigido aparte, es la salvaguarda principal).
DEDUP_SCORE_THRESHOLD = 0.02

DISAMBIGUATION_TRAINING_THRESHOLD = 500


# --------------------------------------------------------------------------- API client


def _api_base_url() -> str:
    settings = get_settings()
    return os.environ.get("KNOWLEDGEOS_API_URL", f"http://localhost:{settings.app_port}")


def _api_token() -> str:
    settings = get_settings()
    return os.environ.get("KNOWLEDGEOS_API_TOKEN", settings.api_token)


def _api_client() -> httpx.Client:
    return httpx.Client(
        base_url=_api_base_url(),
        headers={
            "Authorization": f"Bearer {_api_token()}",
            "X-Agent-Name": "knowledgeos-cli",
        },
        timeout=30.0,
    )


def _connection_error(exc: httpx.RequestError) -> str:
    return (
        f"No se pudo conectar con la API de KnowledgeOS en {_api_base_url()}: {exc}. "
        "Verifica que este corriendo (`python -m knowledgeos.main`)."
    )


# --------------------------------------------------------------------------- export-disambiguations


def cmd_export_disambiguations(args: argparse.Namespace) -> None:
    client = _api_client()
    params: dict[str, Any] = {}
    if args.resolved_only:
        params["resolved_only"] = True

    try:
        resp = client.get("/disambiguations/export", params=params)
        resp.raise_for_status()
    except httpx.RequestError as exc:
        print(_connection_error(exc), file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"La API devolvio {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)

    rows = resp.json()
    out_path = Path(args.output) if args.output else Path("disambiguations_export.jsonl")
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    n = len(rows)
    print(f"Exportadas {n} desambiguaciones a {out_path}")
    print(
        f"({n}/{DISAMBIGUATION_TRAINING_THRESHOLD}) el plan sugiere ~"
        f"{DISAMBIGUATION_TRAINING_THRESHOLD} resoluciones registradas antes de "
        "considerar fine-tuning de un clasificador local (Fase 4, plan_v2.md SS8)."
    )


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace) -> None:
    client = _api_client()
    try:
        resp = client.get("/stats")
        resp.raise_for_status()
    except httpx.RequestError as exc:
        print(_connection_error(exc), file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"La API devolvio {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()

    print("Memorias por contexto")
    print("-" * 60)
    rows = data.get("memories_by_context", [])
    if not rows:
        print("  (sin memorias todavia)")
    for row in rows:
        print(f"  {row['context']:<30} {row['status']:<12} {row['count']}")

    print()
    print("Desambiguaciones (Context Engine, Fase 2/4)")
    print("-" * 60)
    d = data.get("disambiguations", {})
    print(f"  total: {d.get('total', 0)}")
    print(f"    auto (scoring determinista):  {d.get('auto', 0)}")
    print(f"    local_model (Fase 4):         {d.get('local_model', 0)}")
    print(f"    agent (agente/MCP eligio):    {d.get('agent', 0)}")
    print(f"    user:                         {d.get('user', 0)}")
    print(f"    sin resolver:                 {d.get('unresolved', 0)}")

    print()
    print("Preferencias aprendidas (top 20 por peso)")
    print("-" * 60)
    prefs = data.get("preferences_learned", [])[:20]
    if not prefs:
        print("  (ninguna todavia)")
    for p in prefs:
        print(f"  {p['context']:<25} {p['term']:<20} peso={p['weight']:.2f}")


# --------------------------------------------------------------------------- import-markdown


def _collect_memories(files: list[Path]) -> list[tuple[Path, ParsedMemory]]:
    collected: list[tuple[Path, ParsedMemory]] = []
    for f in files:
        try:
            memories = parse_markdown_file(f)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"  ! error leyendo {f}: {exc}", file=sys.stderr)
            continue
        for mem in memories:
            collected.append((f, mem))
    return collected


def _ensure_context(client: httpx.Client, args: argparse.Namespace) -> None:
    resp = client.get("/contexts")
    resp.raise_for_status()
    existing = {c["slug"] for c in resp.json()}
    if args.context in existing:
        return

    if not args.create_context:
        print(
            f"Error: el contexto '{args.context}' no existe. Usa --create-context "
            "para crearlo (opcionalmente con --context-description).",
            file=sys.stderr,
        )
        sys.exit(1)

    description = args.context_description or (
        f"Memorias importadas desde archivos Markdown ({args.path})"
    )
    resp = client.post(
        "/contexts",
        json={"slug": args.context, "name": args.context, "kind": "domain", "description": description},
    )
    if resp.status_code not in (201, 409):
        print(f"Error creando el contexto '{args.context}': {resp.text}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 201:
        print(f"Contexto '{args.context}' creado.")


def _is_duplicate(client: httpx.Client, mem: ParsedMemory, context: str) -> bool:
    try:
        resp = client.get(
            "/memories/search",
            params={"q": mem.content[:500], "context": context, "limit": 1},
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return False  # no bloquear el import por un fallo de busqueda puntual

    results = resp.json().get("results", [])
    if not results:
        return False
    top = results[0]
    return bool(top.get("score", 0.0) >= DEDUP_SCORE_THRESHOLD and top.get("title") == mem.title)


def cmd_import_markdown(args: argparse.Namespace) -> None:
    root = Path(args.path)
    if not root.exists():
        print(f"Error: no existe la ruta '{root}'", file=sys.stderr)
        sys.exit(1)

    files = iter_markdown_files(root)
    if not files:
        print(f"No se encontraron archivos .md en '{root}'")
        return

    memories = _collect_memories(files)
    if not memories:
        print(f"Se leyeron {len(files)} archivo(s) pero no se detecto ninguna memoria importable.")
        return

    if args.dry_run:
        print(f"[dry-run] {len(memories)} memoria(s) detectada(s) en {len(files)} archivo(s):")
        for f, mem in memories:
            mem_type = args.type_ or mem.type
            print(f"  - [{mem_type}] \"{mem.title}\" ({len(mem.content)} chars) <- {f}")
        return

    client = _api_client()
    try:
        _ensure_context(client, args)
    except httpx.RequestError as exc:
        print(_connection_error(exc), file=sys.stderr)
        sys.exit(1)

    imported = duplicated = rejected = 0

    for f, mem in memories:
        mem_type = args.type_ or mem.type

        if _is_duplicate(client, mem, args.context):
            duplicated += 1
            print(f"  = duplicada: \"{mem.title}\"")
            continue

        body: dict[str, Any] = {
            "content": mem.content,
            "context": args.context,
            "type": mem_type,
            "title": mem.title,
        }
        if mem.importance is not None:
            body["importance"] = mem.importance

        try:
            resp = client.post("/memories", json=body)
        except httpx.RequestError as exc:
            rejected += 1
            print(f"  x error de conexion: \"{mem.title}\" -> {exc}")
            continue

        if resp.status_code == 422:
            rejected += 1
            detail = resp.json().get("detail", resp.text)
            print(f"  x rechazada: \"{mem.title}\" -> {detail}")
            continue
        if resp.status_code != 201:
            rejected += 1
            print(f"  x error {resp.status_code}: \"{mem.title}\" -> {resp.text}")
            continue

        imported += 1
        print(f"  + importada: \"{mem.title}\"")

    print()
    print(f"Resumen: {imported} importadas, {duplicated} duplicadas (saltadas), {rejected} rechazadas.")


# --------------------------------------------------------------------------- token (plan_v2.md SS9)


def _token_api_error(resp: httpx.Response) -> None:
    detail = resp.text
    try:
        detail = resp.json().get("detail", resp.text)
    except ValueError:
        pass
    print(f"La API devolvio {resp.status_code}: {detail}", file=sys.stderr)
    sys.exit(1)


def cmd_token_create(args: argparse.Namespace) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    contexts = [c.strip() for c in args.contexts.split(",") if c.strip()] if args.contexts else None

    client = _api_client()
    body: dict[str, Any] = {"name": args.name, "scopes": scopes}
    if contexts is not None:
        body["allowed_contexts"] = contexts

    try:
        resp = client.post("/tokens", json=body)
    except httpx.RequestError as exc:
        print(_connection_error(exc), file=sys.stderr)
        sys.exit(1)
    if resp.status_code != 201:
        _token_api_error(resp)

    data = resp.json()
    contexts_desc = ", ".join(data["allowed_contexts"]) if data.get("allowed_contexts") else "todos"
    print(f"Token creado para '{data['name']}' (scopes: {', '.join(data['scopes'])}; contextos: {contexts_desc}).")
    print()
    print(f"  {data['token']}")
    print()
    print(
        "Guarda este token ahora - KnowledgeOS solo guarda su hash SHA-256 y no puede "
        "volver a mostrarlo. Usalo como KNOWLEDGEOS_API_TOKEN o Authorization: Bearer <token>."
    )


def cmd_token_list(args: argparse.Namespace) -> None:
    client = _api_client()
    try:
        resp = client.get("/tokens")
        resp.raise_for_status()
    except httpx.RequestError as exc:
        print(_connection_error(exc), file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError:
        _token_api_error(resp)

    tokens = resp.json()
    if not tokens:
        print("(sin tokens todavia; el token root de .env sigue funcionando aparte)")
        return

    print(f"{'name':<25} {'scopes':<20} {'contexts':<30} {'estado':<10} created_at")
    print("-" * 110)
    for t in tokens:
        scopes = ",".join(t["scopes"])
        contexts = ",".join(t["allowed_contexts"]) if t.get("allowed_contexts") else "*"
        estado = "revocado" if t.get("revoked_at") else "activo"
        print(f"{t['name']:<25} {scopes:<20} {contexts:<30} {estado:<10} {t['created_at']}")


def cmd_token_revoke(args: argparse.Namespace) -> None:
    client = _api_client()
    try:
        resp = client.delete(f"/tokens/{args.name}")
    except httpx.RequestError as exc:
        print(_connection_error(exc), file=sys.stderr)
        sys.exit(1)
    if resp.status_code != 200:
        _token_api_error(resp)

    print(f"Token '{args.name}' revocado.")


# --------------------------------------------------------------------------- backup / restore


def cmd_backup(args: argparse.Namespace) -> None:
    out_dir = Path(args.output) if args.output else (REPO_ROOT / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"knowledgeos-{timestamp}.sql"

    cmd = ["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", "knowledgeos", "knowledgeos"]
    print(f"Ejecutando: {' '.join(cmd)} > {out_file}")
    try:
        with open(out_file, "wb") as fh:
            result = subprocess.run(cmd, cwd=REPO_ROOT, stdout=fh, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print("Error: no se encontro el comando 'docker'. ¿Docker Desktop esta corriendo?", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        out_file.unlink(missing_ok=True)
        print(f"Error en pg_dump (exit {result.returncode}): {result.stderr.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)

    size = out_file.stat().st_size
    print(f"Backup guardado en {out_file} ({size} bytes)")


def cmd_restore(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: no existe el archivo '{path}'", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        answer = input(
            f"Esto SOBREESCRIBIRA la base de datos 'knowledgeos' con el contenido de "
            f"'{path}'. Esta accion es DESTRUCTIVA e irreversible.\n"
            "Escribe 'yes' para continuar: "
        )
        if answer.strip().lower() != "yes":
            print("Cancelado.")
            return

    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "knowledgeos", "-d", "knowledgeos"]
    print(f"Ejecutando: {' '.join(cmd)} < {path}")
    try:
        with open(path, "rb") as fh:
            result = subprocess.run(cmd, cwd=REPO_ROOT, stdin=fh, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print("Error: no se encontro el comando 'docker'. ¿Docker Desktop esta corriendo?", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"Error en restore (exit {result.returncode}): {result.stderr.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)

    print("Restore completado.")


# --------------------------------------------------------------------------- argparse wiring


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledgeos", description="CLI de KnowledgeOS")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser(
        "export-disambiguations",
        help="Exporta disambiguation_log a JSONL (dataset de la Fase 4)",
    )
    p_export.add_argument("--output", default=None, help="ruta del .jsonl de salida (default: disambiguations_export.jsonl)")
    p_export.add_argument("--resolved-only", action="store_true", help="solo desambiguaciones ya resueltas")
    p_export.set_defaults(func=cmd_export_disambiguations)

    p_stats = sub.add_parser("stats", help="Estadisticas del sistema (igual que GET /stats)")
    p_stats.set_defaults(func=cmd_stats)

    p_import = sub.add_parser(
        "import-markdown",
        help="Importa memorias desde archivos Markdown existentes (Fase 5, conector 1)",
    )
    p_import.add_argument("path", help="archivo .md o directorio (recursivo)")
    p_import.add_argument("--context", required=True, help="slug del contexto destino")
    p_import.add_argument("--type", dest="type_", default=None, help="fuerza el tipo de memoria (default: el que decida el parser, 'semantic')")
    p_import.add_argument("--dry-run", action="store_true", help="solo muestra que se importaria, sin escribir nada")
    p_import.add_argument("--create-context", action="store_true", help="crea el contexto si no existe")
    p_import.add_argument("--context-description", default=None, help="descripcion del contexto nuevo (con --create-context)")
    p_import.set_defaults(func=cmd_import_markdown)

    p_token = sub.add_parser("token", help="Gestion de tokens con scopes (plan_v2.md SS9, requiere auth admin)")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)

    p_token_create = token_sub.add_parser("create", help="Crea un token nuevo (lo imprime UNA vez)")
    p_token_create.add_argument("name", help="identidad del agente, ej. 'claude-desktop' (unica entre tokens activos)")
    p_token_create.add_argument("--scopes", required=True, help="lista separada por comas: read,write,admin")
    p_token_create.add_argument(
        "--contexts", default=None, help="lista de slugs separada por comas; si se omite, el token ve todos los contextos"
    )
    p_token_create.set_defaults(func=cmd_token_create)

    p_token_list = token_sub.add_parser("list", help="Lista tokens (sin hashes ni valores en claro)")
    p_token_list.set_defaults(func=cmd_token_list)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoca un token por nombre")
    p_token_revoke.add_argument("name", help="nombre del token a revocar")
    p_token_revoke.set_defaults(func=cmd_token_revoke)

    p_backup = sub.add_parser("backup", help="pg_dump via docker compose")
    p_backup.add_argument("--output", default=None, help="directorio de salida (default: backups/)")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="Restaura un backup (DESTRUCTIVO)")
    p_restore.add_argument("file", help="archivo .sql generado por 'knowledgeos backup'")
    p_restore.add_argument("--yes", action="store_true", help="omite la confirmacion interactiva")
    p_restore.set_defaults(func=cmd_restore)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
