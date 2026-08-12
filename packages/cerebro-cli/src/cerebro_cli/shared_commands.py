"""Comandos de nivel ecosistema, sin prefijo de modulo (ecosistema-cerebro.md SS11):

- `cerebro backup` / `cerebro restore`: pg_dump/psql via docker compose. Un solo
  Postgres compartido (SS8) significa que un solo dump cubre ambos schemas
  (`cerebro_memory` y `cerebro_docs`) en una operacion -- portado tal cual del
  `cerebro_memory.cli` original, sin cambios de mecanismo (SS9).
- `cerebro token create/revoke`: TRANSVERSAL (SS13) -- un secreto, registrado por
  separado en ambas APIs. Ver `tokens.py` para el porque del estado pendiente que
  hace que reintentar el mismo comando tras un fallo parcial sea seguro.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cerebro_clients import CerebroAPIError, CerebroConnectionError, DocsClient, MemoryClient

from cerebro_cli.tokens import clear_pending_value, generate_transversal_token, load_pending_value, save_pending_value

# packages/cerebro-cli/src/cerebro_cli/shared_commands.py -> parents[4] es la raiz del
# monorepo (donde vive compose.yaml) - mismo calculo que REPO_ROOT en el cli.py
# original de cerebro-memory, solo que este archivo esta un nivel mas adentro.
REPO_ROOT = Path(__file__).resolve().parents[4]

POSTGRES_USER = "knowledgeos"  # nombre del servicio/usuario/DB en compose.yaml - sin cambios (SS5)
POSTGRES_DB = "knowledgeos"


# --------------------------------------------------------------------------- backup / restore


def cmd_backup(args: argparse.Namespace) -> None:
    out_dir = Path(args.output) if args.output else (REPO_ROOT / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"cerebro-{timestamp}.sql"

    cmd = ["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", POSTGRES_USER, POSTGRES_DB]
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
    print(f"Backup guardado en {out_file} ({size} bytes) - cubre cerebro_memory y cerebro_docs (un solo Postgres compartido).")


def cmd_restore(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: no existe el archivo '{path}'", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        answer = input(
            f"Esto SOBREESCRIBIRA la base de datos '{POSTGRES_DB}' (schemas cerebro_memory y "
            f"cerebro_docs) con el contenido de '{path}'. Esta accion es DESTRUCTIVA e "
            "irreversible.\nEscribe 'yes' para continuar: "
        )
        if answer.strip().lower() != "yes":
            print("Cancelado.")
            return

    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB]
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


# --------------------------------------------------------------------------- token transversal (SS13)


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def cmd_token_create(
    args: argparse.Namespace,
    *,
    memory_client: MemoryClient | None = None,
    docs_client: DocsClient | None = None,
) -> None:
    """Registra UN secreto en cerebro-memory y cerebro-docs (SS13).

    Fallo parcial: reporta explicitamente el estado de cada servicio y termina con
    exit code != 0 si alguno fallo. El secreto generado se persiste localmente
    (`cerebro_cli.tokens`) hasta que AMBOS servicios confirman exito, para que
    reintentar el mismo comando reuse el mismo secreto -- el registro es idempotente
    por nombre en cada API (mismo nombre + mismo hash no duplica fila).
    """
    scopes = _split_csv(args.scopes) or []
    contexts = _split_csv(args.contexts)
    categories = _split_csv(args.categories)

    pending = load_pending_value(args.name)
    if pending is not None:
        value = pending
        print(f"Reanudando el registro pendiente de '{args.name}' (mismo secreto de un intento anterior).")
    else:
        value = generate_transversal_token()
        save_pending_value(args.name, value)

    memory_client = memory_client or MemoryClient()
    docs_client = docs_client or DocsClient()

    results: dict[str, str] = {}

    try:
        memory_client.create_token(args.name, scopes, allowed_contexts=contexts, value=value)
        results["cerebro-memory"] = "ok"
    except CerebroConnectionError as exc:
        results["cerebro-memory"] = f"error de conexion: {exc}"
    except CerebroAPIError as exc:
        results["cerebro-memory"] = f"error {exc.status_code}: {exc.detail}"

    try:
        docs_client.create_token(args.name, scopes, allowed_categories=categories, value=value)
        results["cerebro-docs"] = "ok"
    except CerebroConnectionError as exc:
        results["cerebro-docs"] = f"error de conexion: {exc}"
    except CerebroAPIError as exc:
        results["cerebro-docs"] = f"error {exc.status_code}: {exc.detail}"

    print(f"Registro de token transversal '{args.name}' (scopes: {', '.join(scopes)}):")
    for service, status in results.items():
        marker = "+" if status == "ok" else "x"
        print(f"  {marker} {service}: {status}")

    if all(status == "ok" for status in results.values()):
        clear_pending_value(args.name)
        print()
        print(f"  {value}")
        print()
        print(
            "Guarda este token ahora - ningun servicio vuelve a mostrarlo. Usalo como "
            "CEREBRO_TOKEN (valido para cerebro-memory y cerebro-docs)."
        )
        return

    print()
    print(
        f"Registro parcial: reintenta `cerebro token create {args.name} --scopes {args.scopes}` "
        "(reusa el mismo secreto, es seguro) para completar el servicio que fallo, o revoca "
        "manualmente el que si quedo registrado.",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_token_revoke(
    args: argparse.Namespace,
    *,
    memory_client: MemoryClient | None = None,
    docs_client: DocsClient | None = None,
) -> None:
    """Revoca por nombre en ambos servicios (SS13). Un 404 (ya revocado/inexistente en
    ese servicio) cuenta como exito -- el estado deseado ("no activo ahi") ya se
    cumple, y hace que reintentar tras un fallo parcial tambien sea seguro."""
    memory_client = memory_client or MemoryClient()
    docs_client = docs_client or DocsClient()

    results: dict[str, str] = {}

    try:
        memory_client.revoke_token(args.name)
        results["cerebro-memory"] = "ok"
    except CerebroConnectionError as exc:
        results["cerebro-memory"] = f"error de conexion: {exc}"
    except CerebroAPIError as exc:
        if exc.status_code == 404:
            results["cerebro-memory"] = "ok (ya no estaba activo)"
        else:
            results["cerebro-memory"] = f"error {exc.status_code}: {exc.detail}"

    try:
        docs_client.revoke_token(args.name)
        results["cerebro-docs"] = "ok"
    except CerebroConnectionError as exc:
        results["cerebro-docs"] = f"error de conexion: {exc}"
    except CerebroAPIError as exc:
        if exc.status_code == 404:
            results["cerebro-docs"] = "ok (ya no estaba activo)"
        else:
            results["cerebro-docs"] = f"error {exc.status_code}: {exc.detail}"

    print(f"Revocacion de token transversal '{args.name}':")
    for service, status in results.items():
        marker = "+" if status.startswith("ok") else "x"
        print(f"  {marker} {service}: {status}")

    if not all(status.startswith("ok") for status in results.values()):
        print()
        print(f"Revocacion parcial: reintenta `cerebro token revoke {args.name}`.", file=sys.stderr)
        sys.exit(1)
