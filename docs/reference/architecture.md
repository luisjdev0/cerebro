# Architecture, portal and API docs

> Component diagram, the web portal and where the API documentation lives. Moved out of the README; see the [documentation index](../README.md).


```
Agent (Claude / GPT / Gemini / custom)
        |
        v
   MCP Server (stdio)  -----------------------  packages/cerebro-mcp/src/cerebro_mcp/server.py
   or CLI               -----------------------  packages/cerebro-cli/src/cerebro_cli/main.py
        |                                        both are thin adapters, no business logic
        v                                        of their own (except import-markdown / backup-restore)
  cerebro-clients (shared httpx SDK)  ---------  packages/cerebro-clients/src/cerebro_clients/
        |
        +----------------------------+----------------------------+
        v                                                         v
  cerebro-memory API (FastAPI)                          cerebro-docs API (FastAPI)
  packages/cerebro-memory/src/cerebro_memory/api.py      packages/cerebro-docs/src/cerebro_docs/api.py
        |                                                         |
        +--> Hybrid retrieval: vector+full-text+RRF      retrieval.py
        +--> Context Engine: scoping and disambiguation   context_engine.py
        +--> Relationships / lightweight graph (edges+timeline) graph.py
        +--> Optional local classifier (OFF by default)   context_engine.py (Phase 4)
        |                                                         |
        |                                                +--> categories + documents
        |                                                +--> versioning (document_versions)
        |                                                +--> partial per-section patches
        v                                                         v
  PostgreSQL + pgvector, schema `cerebro_memory`         same Postgres, schema `cerebro_docs`
  (contexts, memories, audit_log, disambiguation_log,    (categories, documents, document_versions,
   context_preferences, memory_edges, api_tokens)         api_tokens)
```

Qdrant, Redis, and a local auxiliary model running by default **do not appear** in the
compose on purpose ("earn your complexity"): pgvector covers a single user's memory
volume with single-digit-ms latencies, and the plug-in point for a
local classifier (Ollama) exists but stays off until there is a real dataset that
justifies it (see "Optional local classifier" below).

`cerebro-memory` and `cerebro-docs` share the same Postgres instance (same
`DATABASE_URL`/`POSTGRES_PASSWORD`) but each lives in its own schema and applies
its own migrations on startup — they are independent stateless services, not a
monolith split into two processes that coordinate at runtime.

## Web portal (`cerebro-portal`)

A browser UI over the same APIs and session tokens (React + Vite + TypeScript + Tailwind with
the Ixchel components, English/Spanish, light/dark). It is a static SPA built into an nginx
image and served by the gateway at its root, so everything is same-origin (`/memory`, `/docs`,
`/flows`, `/auth`) and there is nothing extra to expose. With the `full` profile it comes up
with the rest: open `http://localhost:8080/`.

- **Everyone**: Overview (counters and activity), Memory, Documents, Flows (definitions, diagrams,
  runs), limited to the modules and contexts/categories the account may see.
- **Admins**: Users & access, API tokens (create / switch on-off / revoke), Logs (raw lines per
  module that records them) and Backup (`POST /auth/backup` as a download).
- **Sign-in**: username + password (`POST /auth/login/password`), which mints a session token
  (12 h for the portal; the CLI asks for a sliding 90-day session, see the user manual). The deployment's **master user** is defined in `.env` (`ADMIN_USER`/`ADMIN_PASSWORD`),
  checked on every login like the root token and never stored; it creates everyone else.
- Portal-only endpoints (`GET /logs` in memory and flows, flow categories and runs listing,
  user/group/token management) deliberately have no client/MCP/CLI mirror.

Develop it against a running gateway:

```bash
cd packages/cerebro-portal
pnpm install
VITE_GATEWAY_URL=http://localhost:8080 pnpm dev   # proxies /api and /mcp
pnpm typecheck && pnpm test && pnpm build          # what CI runs (the `portal` job)
```

## API documentation and Postman

Every instance serves the API reference at `/api/` (Swagger UI). For Postman, import
`docs/postman/cerebro.postman_collection.json`: set `baseUrl` to your gateway and `token` to an API
token (or run *Auth > Sign in with password*). The file is generated from the services' OpenAPI
documents (`python scripts/generate_postman.py`) and CI keeps it in sync.
