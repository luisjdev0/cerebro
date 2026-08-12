"""Subcomandos `cerebro memory <subcomando>` -- port 1:1 del `cerebro_memory.cli`
original, sustituyendo las llamadas httpx directas por `MemoryClient`
(`cerebro_clients`). El unico calculo propio que queda aqui (fuera del cliente) es la
orquestacion del importador de Markdown (`_collect_memories`/`_ensure_context`/
`_is_duplicate`), igual que en el original: el parseo puro sigue viviendo en
`cerebro_memory.markdown_importer`, no se duplica.

`token create/list/revoke` aqui son ESCOPADOS a cerebro-memory unicamente (mismo
comportamiento que el `cerebro-memory token` original) -- distintos de `cerebro token
create/revoke` a nivel raiz, que son TRANSVERSALES (ver shared_commands.py y
ecosistema-cerebro.md SS13).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cerebro_clients import CerebroAPIError, CerebroConnectionError, MemoryClient
from cerebro_memory.markdown_importer import ParsedMemory, iter_markdown_files, parse_markdown_file

# RRF score de un hit que gana el rank #1 tanto en la busqueda vectorial como en full
# text es ~2/(60+1) ~= 0.033 (ver cerebro_memory.retrieval.reciprocal_rank_fusion,
# k=60 default). Un duplicado casi exacto del mismo contenido deberia aterrizar ahi;
# ponemos el umbral algo por debajo para tolerar variacion menor sin abrir la puerta a
# falsos positivos (el requisito de titulo identico, exigido aparte, es la salvaguarda
# principal).
DEDUP_SCORE_THRESHOLD = 0.02

DISAMBIGUATION_TRAINING_THRESHOLD = 500


def _client() -> MemoryClient:
    return MemoryClient()


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        data = client.get_stats()
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-memory: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

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


# --------------------------------------------------------------------------- export-disambiguations


def cmd_export_disambiguations(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        rows = client.export_disambiguations(resolved_only=args.resolved_only)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-memory: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

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


def _ensure_context(client: MemoryClient, args: argparse.Namespace) -> None:
    existing = {c["slug"] for c in client.list_contexts()}
    if args.context in existing:
        return

    if not args.create_context:
        print(
            f"Error: el contexto '{args.context}' no existe. Usa --create-context "
            "para crearlo (opcionalmente con --context-description).",
            file=sys.stderr,
        )
        sys.exit(1)

    description = args.context_description or f"Memorias importadas desde archivos Markdown ({args.path})"
    try:
        client.create_context(args.context, args.context, "domain", description=description)
        print(f"Contexto '{args.context}' creado.")
    except CerebroAPIError as exc:
        if exc.status_code != 409:
            print(f"Error creando el contexto '{args.context}': {exc.detail}", file=sys.stderr)
            sys.exit(1)


def _is_duplicate(client: MemoryClient, mem: ParsedMemory, context: str) -> bool:
    try:
        data = client.search_memories(mem.content[:500], context=context, limit=1)
    except (CerebroConnectionError, CerebroAPIError):
        return False  # no bloquear el import por un fallo de busqueda puntual

    results = data.get("results", [])
    if not results:
        return False
    top = results[0]
    return bool(top.get("score", 0.0) >= DEDUP_SCORE_THRESHOLD and top.get("title") == mem.title)


def cmd_import_markdown(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
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

    client = client or _client()
    try:
        _ensure_context(client, args)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-memory: {exc}", file=sys.stderr)
        sys.exit(1)

    imported = duplicated = rejected = 0

    for f, mem in memories:
        mem_type = args.type_ or mem.type

        if _is_duplicate(client, mem, args.context):
            duplicated += 1
            print(f"  = duplicada: \"{mem.title}\"")
            continue

        try:
            client.create_memory(
                mem.content, args.context, mem_type, title=mem.title, importance=mem.importance
            )
        except CerebroConnectionError as exc:
            rejected += 1
            print(f"  x error de conexion: \"{mem.title}\" -> {exc}")
            continue
        except CerebroAPIError as exc:
            rejected += 1
            print(f"  x rechazada ({exc.status_code}): \"{mem.title}\" -> {exc.detail}")
            continue

        imported += 1
        print(f"  + importada: \"{mem.title}\"")

    print()
    print(f"Resumen: {imported} importadas, {duplicated} duplicadas (saltadas), {rejected} rechazadas.")


# --------------------------------------------------------------------------- token (escopado a memory)


def cmd_token_create(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    contexts = [c.strip() for c in args.contexts.split(",") if c.strip()] if args.contexts else None

    try:
        data = client.create_token(args.name, scopes, allowed_contexts=contexts)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-memory: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    contexts_desc = ", ".join(data["allowed_contexts"]) if data.get("allowed_contexts") else "todos"
    print(f"Token creado para '{data['name']}' (scopes: {', '.join(data['scopes'])}; contextos: {contexts_desc}).")
    print()
    print(f"  {data['token']}")
    print()
    print(
        "Guarda este token ahora - cerebro-memory solo guarda su hash SHA-256 y no puede "
        "volver a mostrarlo. Usalo como CEREBRO_TOKEN o Authorization: Bearer <token> "
        "(valido solo para cerebro-memory; para un token que funcione en ambos servicios "
        "usa `cerebro token create`, sin el subcomando `memory`)."
    )


def cmd_token_list(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        tokens = client.list_tokens()
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-memory: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

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


def cmd_token_revoke(args: argparse.Namespace, *, client: MemoryClient | None = None) -> None:
    client = client or _client()
    try:
        client.revoke_token(args.name)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-memory: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print(f"Token '{args.name}' revocado (cerebro-memory).")
