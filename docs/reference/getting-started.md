# Getting started (detailed)

> Detailed setup paths (local development, full Docker stack, connect-only) and quick API usage. Moved out of the README; see the [documentation index](../README.md).

## Quickstart

Three paths depending on what you want to do - pick one:

### A. Local development (recommended for dogfooding / continued development)

Both APIs running directly with Python, only Postgres in Docker. It's the fastest
mode for iterating (instant reload, logs in your own terminal).

Requirements: Docker Desktop running, Python 3.11+.

```bash
# 1. Database (docker compose without --profile only brings up postgres)
docker compose up -d
docker compose ps   # wait until it is "healthy"

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# install all 5 packages in a single command (necessary: cerebro-cli and cerebro-mcp
# depend on cerebro-clients/cerebro-memory by name, they are not on PyPI, so
# pip only resolves them against each other if they're all passed together)
pip install -e "packages/cerebro-clients[dev]" -e "packages/cerebro-memory[dev]" ^
            -e "packages/cerebro-docs[dev]" -e "packages/cerebro-mcp[dev]" ^
            -e "packages/cerebro-cli[dev]"
# Linux/Mac: same command but with \ instead of ^ as the line continuator

# 3. Configuration (shared variables: DATABASE_URL, API_TOKEN, APP_PORT, etc.)
cp .env.example .env
# the default values already work against this repo's compose.yaml;
# change API_TOKEN before exposing any service outside your machine (see "Security").

# 4. Start cerebro-memory (applies migrations automatically on startup, port 8000)
python -m cerebro_memory.main
# or: uvicorn cerebro_memory.main:app --reload

# 5. Start cerebro-docs in ANOTHER terminal, with its own port/token (shares
# DATABASE_URL/POSTGRES_PASSWORD via the same .env, but needs its own APP_PORT/
# API_TOKEN so it doesn't collide with cerebro-memory's - see the "cerebro-docs"
# section of .env.example):
$env:APP_PORT=8010; $env:API_TOKEN="change-me-dev-token-docs"; python -m cerebro_docs.main   # PowerShell
# APP_PORT=8010 API_TOKEN=change-me-dev-token-docs python -m cerebro_docs.main               # bash
```

The first time `cerebro-memory` starts, it downloads the embeddings model
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` via `fastembed`, ~9s,
then it stays cached locally by `fastembed`/`huggingface_hub`). `cerebro-docs` does
not use embeddings (simple full-text), it starts instantly.

### B. Everything in Docker (production / testing the real deploy)

Postgres, Redis, and the three APIs in containers, without installing Python on the
host. Each API is served from its own multi-stage image (`packages/cerebro-memory/Dockerfile`,
`packages/cerebro-docs/Dockerfile`, `packages/cerebro-flows/Dockerfile`); the
`cerebro-memory` one pre-downloads the embeddings model *at build time*, so the
container starts in seconds, not minutes.

```bash
cp .env.example .env   # and change API_TOKEN

# brings up all three APIs + the gateway (the "full" profile adds all of them; without the
# flag, `docker compose up -d` still only brings up postgres+redis, the dev mode above)
docker compose --profile full up -d
docker compose --profile full ps   # wait until all 4 are "healthy"

curl http://localhost:8005/health   # cerebro-memory (host 8005 -> container 8000)
curl http://localhost:8006/health   # cerebro-docs   (host 8006 -> container 8000)
curl http://localhost:8007/health   # cerebro-flows  (host 8007 -> container 8000)

# equivalent, ALL through the gateway (host 8080 -> container 80, see below):
curl http://localhost:8080/memory/health
curl http://localhost:8080/docs/health
curl http://localhost:8080/flows/health
```

To rebuild an image after a code change:
`docker compose --profile full build cerebro-memory-api` (or `cerebro-docs-api`/
`cerebro-flows-api`).

Then open `http://localhost:8080/` for the web portal (see "Web portal" below). Set
`ADMIN_USER` and `ADMIN_PASSWORD` in `.env` first: that is the account you sign in with.

#### Exposing it behind a single reverse proxy (`gateway`)

The `gateway` service (Caddy, `gateway/Caddyfile`) routes by prefix to each internal
API and exposes everything on a single port (`CEREBRO_GATEWAY_HOST_PORT`, default `8080`):
`/api/memory` → `cerebro-memory-api`, `/api/docs` → `cerebro-docs-api`, `/api/flows` →
`cerebro-flows-api`, `/api/auth` → `cerebro-auth-api`. **`/api/` is the API documentation**
(Swagger UI with a how-to and a token box, no internet needed), **`/mcp` is the MCP server over
HTTP** (see "MCP over HTTP" below), and everything else is the web portal. The old prefixes
without `/api` (`/memory`, `/docs`, `/flows`, `/auth`) remain as aliases, so nothing existing
breaks. The
idea is that if you're going to expose this behind your own Caddy/nginx (a VPS, whatever),
that external reverse proxy only needs **one** block pointing at the gateway's port,
instead of one block per service -- no matter how many modules the ecosystem has.
It doesn't replace each API's direct ports (they're still there for local dev or
direct access within the compose network); it's an additional, optional layer. With
`CEREBRO_MEMORY_URL=https://your-domain/api/memory` (same pattern for `_DOCS_`/`_FLOWS_`),
`MemoryClient`/`DocsClient`/`FlowsClient` need no code change at all -- they already
build the final URL by concatenating `base_url` + relative path. See "HTTPS with Caddy" in
`DEPLOY.md` for the full example with a real domain.

### C. I just want to connect Claude/an agent via MCP or CLI (I already have the APIs running elsewhere)

You don't need to clone the backend - just the client layer:

```bash
pip install -e "packages/cerebro-clients[dev]" -e "packages/cerebro-mcp[dev]"
# or, for the CLI instead of the MCP server (cerebro-cli also needs cerebro-memory
# installed, only to reuse its Markdown parser - see "CLI" below):
pip install -e "packages/cerebro-clients[dev]" -e "packages/cerebro-memory[dev]" -e "packages/cerebro-cli[dev]"
```

Go directly to "Connecting to Claude (MCP server)" below, pointing
`CEREBRO_MEMORY_URL`/`CEREBRO_DOCS_URL` at the already-deployed APIs (mode A or B, yours or
a third party's) and `CEREBRO_TOKEN` with a token of the appropriate scope (see "Security" - you
usually won't want to give the root token to every agent).

---

`GET /health` does not require auth in either API; the rest of the endpoints
require `Authorization: Bearer <token>` (see "Security").

## Quick usage

```bash
# --- cerebro-memory (mode A, local port 8000) ---
TOKEN=change-me-dev-token

curl -s -X POST localhost:8000/contexts \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"finanzas-personales","name":"Finanzas personales","kind":"domain","description":"Gastos e ingresos personales"}'

curl -s -X POST localhost:8000/memories \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"Este mes gaste 450 dolares en supermercado","context":"finanzas-personales","type":"episodic"}'

curl -s "localhost:8000/memories/search?q=cuanto+gaste+este+mes&context=finanzas-personales" \
  -H "Authorization: Bearer $TOKEN"

# --- cerebro-docs (mode A, local port 8010) ---
DOCS_TOKEN=change-me-dev-token-docs

curl -s -X POST localhost:8010/categories \
  -H "Authorization: Bearer $DOCS_TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"infraestructura","name":"Infraestructura","description":"Runbooks y notas de infra"}'

curl -s -X POST localhost:8010/documents \
  -H "Authorization: Bearer $DOCS_TOKEN" -H "Content-Type: application/json" \
  -d '{"category":"infraestructura","title":"Runbook: restore de Postgres","content":"# Runbook\n\n## Pasos\n1. ..."}'

curl -s "localhost:8010/documents?q=restore+postgres" -H "Authorization: Bearer $DOCS_TOKEN"
```

In Docker (mode B), replace `localhost:8000` with `localhost:8005` and
`localhost:8010` with `localhost:8006`.
