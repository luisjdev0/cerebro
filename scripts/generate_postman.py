#!/usr/bin/env python3
"""Generate the Postman collection from the four services' OpenAPI documents.

    python scripts/generate_postman.py            # rewrite docs/postman/cerebro.postman_collection.json
    python scripts/generate_postman.py --check    # exit 1 if the committed file is out of date (CI)

The collection is derived, never edited by hand: every request comes from the live FastAPI
schema of cerebro-memory/docs/flows/auth, so it cannot drift from the code as long as it is
regenerated (and CI fails when it is not). It needs the four packages installed
(`pip install -e packages/cerebro-{auth,memory,docs,flows}`); no database is contacted.

Conventions of the collection: `{{baseUrl}}` is the gateway (default http://localhost:8080),
every service lives under `{{baseUrl}}/api/<service>`, and a collection-level Bearer auth sends
`{{token}}`. "Auth / Sign in with password" stores the session token it receives in `token`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "postman" / "cerebro.postman_collection.json"

SERVICES = [
    ("memory", "Memory", "cerebro_memory.api"),
    ("docs", "Documents", "cerebro_docs.api"),
    ("flows", "Flows", "cerebro_flows.api"),
    ("auth", "Auth", "cerebro_auth.api"),
]
METHODS = ("get", "post", "put", "patch", "delete")
MAX_DEPTH = 5


def load_spec(module: str) -> dict[str, Any]:
    import importlib

    return importlib.import_module(module).create_app().openapi()


# --------------------------------------------------------------------------- examples


def resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def example(schema: dict[str, Any], spec: dict[str, Any], depth: int = 0) -> Any:
    """A small, deterministic sample value for a JSON schema."""
    schema = resolve(schema, spec)
    if "example" in schema:
        return schema["example"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if resolve(o, spec).get("type") != "null"]
            return example(options[0], spec, depth) if options else None
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            value = example(part, spec, depth)
            if isinstance(value, dict):
                merged.update(value)
        return merged
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if depth >= MAX_DEPTH:
            return {}
        return {
            name: example(sub, spec, depth + 1) for name, sub in (schema.get("properties") or {}).items()
        }
    if kind == "array":
        return [] if depth >= MAX_DEPTH else [example(schema.get("items", {}), spec, depth + 1)]
    if kind == "integer":
        return schema.get("minimum", 0)
    if kind == "number":
        return schema.get("minimum", 0.0)
    if kind == "boolean":
        return False
    fmt = schema.get("format")
    if fmt == "date-time":
        return "2026-01-01T00:00:00Z"
    if fmt == "date":
        return "2026-01-01"
    if fmt == "uuid":
        return "00000000-0000-0000-0000-000000000000"
    return "string"


# --------------------------------------------------------------------------- requests


def url_object(service: str, path: str, query: list[dict[str, Any]]) -> dict[str, Any]:
    segments = [s for s in path.split("/") if s]
    parts = [f":{s[1:-1]}" if s.startswith("{") else s for s in segments]
    raw = "{{baseUrl}}/api/" + service + ("/" + "/".join(parts) if parts else "")
    url: dict[str, Any] = {"raw": raw, "host": ["{{baseUrl}}"], "path": ["api", service, *parts]}
    path_vars = [
        {"key": s[1:-1], "value": "", "description": "path parameter"} for s in segments if s.startswith("{")
    ]
    if path_vars:
        url["variable"] = path_vars
    if query:
        url["query"] = query
        required_qs = "&".join(f"{q['key']}={q['value']}" for q in query if not q.get("disabled"))
        if required_qs:
            url["raw"] += "?" + required_qs
    return url


def build_request(service: str, path: str, method: str, op: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    query: list[dict[str, Any]] = []
    header: list[dict[str, Any]] = []
    for param in op.get("parameters", []):
        param = resolve(param, spec)
        if param["in"] == "query":
            value = example(param.get("schema", {}), spec)
            query.append(
                {
                    "key": param["name"],
                    "value": "" if value is None else str(value).lower() if isinstance(value, bool) else str(value),
                    "disabled": not param.get("required", False),
                    "description": param.get("description", ""),
                }
            )
        elif param["in"] == "header" and param["name"].lower() != "authorization":
            header.append(
                {"key": param["name"], "value": str(example(param.get("schema", {}), spec)), "disabled": True}
            )
    request: dict[str, Any] = {
        "method": method.upper(),
        "header": header,
        "url": url_object(service, path, query),
        "description": (op.get("description") or op.get("summary") or "").strip(),
    }
    content = (op.get("requestBody") or {}).get("content", {})
    if "application/json" in content:
        request["header"].append({"key": "Content-Type", "value": "application/json"})
        body = example(content["application/json"].get("schema", {}), spec)
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2, ensure_ascii=False),
            "options": {"raw": {"language": "json"}},
        }
    elif "multipart/form-data" in content:
        schema = resolve(content["multipart/form-data"].get("schema", {}), spec)
        fields = []
        for name, sub in (schema.get("properties") or {}).items():
            sub = resolve(sub, spec)
            is_file = sub.get("format") == "binary" or sub.get("type") == "string" and "contentMediaType" in sub
            fields.append({"key": name, "type": "file" if is_file else "text", **({} if is_file else {"value": ""})})
        request["body"] = {"mode": "formdata", "formdata": fields}
    return request


def build_item(service: str, path: str, method: str, op: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    name = op.get("summary") or f"{method.upper()} {path}"
    item: dict[str, Any] = {"name": f"{name}", "request": build_request(service, path, method, op, spec)}
    # Sign in: keep the session token for every other request.
    if service == "auth" and path == "/login/password" and method == "post":
        item["name"] = "Sign in with password"
        item["event"] = [
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "if (pm.response.code === 200) {",
                        "  pm.collectionVariables.set('token', pm.response.json().token);",
                        "}",
                    ],
                },
            }
        ]
        item["request"]["auth"] = {"type": "noauth"}
    return item


def build_service_folder(service: str, label: str, module: str) -> dict[str, Any]:
    spec = load_spec(module)
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(spec["paths"]):
        for method in METHODS:
            op = spec["paths"][path].get(method)
            if op is None:
                continue
            first = next((s for s in path.split("/") if s and not s.startswith("{")), "") or "root"
            groups.setdefault(first, []).append(build_item(service, path, method, op, spec))
    folder_items: list[dict[str, Any]] = []
    for group in sorted(groups):
        items = groups[group]
        folder_items.extend(items) if len(groups) == 1 else folder_items.append({"name": group, "item": items})
    return {"name": label, "description": f"cerebro-{service} (served at /api/{service})", "item": folder_items}


def build_collection() -> dict[str, Any]:
    return {
        "info": {
            "name": "cerebro",
            "description": (
                "Generated from the services' OpenAPI documents by scripts/generate_postman.py -- do not edit by hand.\n\n"
                "1. Set `baseUrl` (the gateway, default http://localhost:8080).\n"
                "2. Set `token` to an API token, or run Auth > Sign in with password to fetch a session token.\n"
                "Every service lives under {{baseUrl}}/api/<service>."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
        "variable": [
            {"key": "baseUrl", "value": "http://localhost:8080"},
            {"key": "token", "value": ""},
        ],
        "item": [build_service_folder(*svc) for svc in SERVICES],
    }


def render() -> str:
    return json.dumps(build_collection(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if the committed collection is out of date")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(
                f"{OUT.relative_to(ROOT)} is out of date: run `python scripts/generate_postman.py` and commit it.",
                file=sys.stderr,
            )
            return 1
        print("Postman collection is up to date.")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
