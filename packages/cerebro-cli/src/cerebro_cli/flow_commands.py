"""`cerebro flow <subcommand>` subcommands (luisjdev-pendientes/cerebro-flows): CRUD
for flow categories/definitions via `FlowsClient` (`cerebro_clients`), with no
business logic of its own beyond reading YAML from a file and formatting console
output. No commands to RUN flows (flow_start/flow_next) -- a flow is driven
by a model turn by turn via the MCP tools; it makes no sense typed by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cerebro_clients import CerebroAPIError, CerebroConnectionError, FlowsClient


def _client() -> FlowsClient:
    return FlowsClient()


def _read_yaml(args: argparse.Namespace) -> str:
    path = Path(args.yaml_file)
    if not path.exists():
        print(f"Error: no existe el archivo '{path}'", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _print_flow(flow: dict) -> None:
    print(f"[{flow['category']}] {flow['code']} - {flow['name']}  (v{flow['current_version']}, {flow['status']})")
    print(f"  id: {flow['id']}")


def _print_flow_list(flows: list[dict]) -> None:
    if not flows:
        print("(sin flujos)")
        return
    for f in flows:
        print(f"[{f['category']}] {f['code']} - {f['name']}  (v{f['current_version']}, {f['status']})")


# --------------------------------------------------------------------------- category


def cmd_category_create(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        category = client.create_category(args.slug, args.code, args.name or args.slug, description=args.description)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Categoria '{category['slug']}' (code={category['code']}) creada.")


def cmd_category_list(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        categories = client.list_categories()
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if not categories:
        print("(sin categorias todavia)")
        return
    print(f"{'slug':<20} {'code':<8} {'name':<25} description")
    print("-" * 90)
    for c in categories:
        print(f"{c['slug']:<20} {c['code']:<8} {c['name']:<25} {c.get('description') or ''}")


# --------------------------------------------------------------------------- flow definitions


def cmd_validate(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    yaml_content = _read_yaml(args)
    try:
        client.validate_flow(yaml_content)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"Invalido: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print("Valido.")


def cmd_save(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    yaml_content = _read_yaml(args)
    try:
        flow = client.create_flow(args.category, yaml_content, code=args.code)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Flujo guardado: {flow['code']} (id={flow['id']}).")


def cmd_get(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        flow = client.get_flow(args.code)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(flow["yaml_content"])


def cmd_list(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        flows = client.list_flows(category=args.category, limit=args.limit, offset=args.offset)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    _print_flow_list(flows)


def cmd_update(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    yaml_content = _read_yaml(args)
    try:
        flow = client.update_flow(args.code, yaml_content)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Flujo {flow['code']} actualizado a v{flow['current_version']}.")


def cmd_delete(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    if not args.yes:
        answer = input(f"Esto borrara el flujo {args.code} (y su historial de versiones/ejecuciones). Escribe 'yes' para continuar: ")
        if answer.strip().lower() != "yes":
            print("Cancelado.")
            return

    try:
        client.delete_flow(args.code)
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Flujo {args.code} borrado.")


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace, *, client: FlowsClient | None = None) -> None:
    client = client or _client()
    try:
        data = client.get_stats()
    except CerebroConnectionError as exc:
        print(f"No se pudo conectar con cerebro-flows: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"La API devolvio {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print("Estadisticas de cerebro-flows")
    print("-" * 40)
    print(f"  categorias: {data['categories']}")
    print(f"  flujos:     {data['flows']}")
    print(f"  runs:       {data['runs']}")
