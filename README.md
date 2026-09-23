# cerebro

Self-hosted, model-agnostic ecosystem for persistent memory and documentation
for AI agents (Claude, GPT, Gemini, custom agents). It started as a single package
(`knowledgeos`) and is now a monorepo of six packages under `packages/`:

| Package | What it is | Entry point |
|---|---|---|
| [`cerebro-memory`](packages/cerebro-memory) | Persistent memory API service: PostgreSQL + pgvector, hybrid retrieval (vector + Spanish full-text, fused with RRF), lifecycle by supersession, audit log, Context Engine (LLM-free context disambiguation), lightweight relationship graph + timeline, scoped tokens | *(none — pure API service, no CLI or MCP of its own)* |
| [`cerebro-docs`](packages/cerebro-docs) | Sibling API service: repository of full, categorizable, versioned Markdown documents, with partial per-section patches, archiving, hidden categories, slug redirects, and full-text search | *(none — pure API service)* |
| [`cerebro-flows`](packages/cerebro-flows) | Sibling API service: "traffic light" engine that reveals a process defined in YAML step by step to a model (never the full definition) — decisions, approval checkpoints, parallel delegation. Postgres for what's immutable + Redis only for the mutable pointer of a run | *(none — pure API service)* |
| [`cerebro-clients`](packages/cerebro-clients) | Shared thin `httpx` SDK (`MemoryClient`, `DocsClient`, `FlowsClient`) that talks to all three APIs | *(library, not executable)* |
| [`cerebro-mcp`](packages/cerebro-mcp) | Single stdio MCP server exposing the three services as tools (`memory_*` + `docs_*` + `flow_*`) | `cerebro-mcp` |
| [`cerebro-cli`](packages/cerebro-cli) | Single CLI (`cerebro memory ...`, `cerebro docs ...`, `cerebro flow ...`, plus cross-cutting commands) | `cerebro` |

`cerebro-memory`, `cerebro-docs`, and `cerebro-flows` have no business logic
duplicated among them: each owns its own schema in the same Postgres
(`cerebro_memory` / `cerebro_docs` / `cerebro_flows`) and its own auth. Every client
(`cerebro-mcp`, `cerebro-cli`, or any future integration) goes through
`cerebro-clients` and the same HTTP path for auth + scopes + audit log of each API
— there are no shortcuts or duplicated business logic in the client layer.

v1.0 of `cerebro-memory` = solid Phases 1-3 + passing evaluation + sustained
dogfooding (see `plan_v2.md` SS8, at the repo root, for the full architecture and
data model of that service); the local classifier, the graph, and external
connectors are improvements on top of that base, not requirements. `cerebro-docs` is
more recent and simpler: no semantic retrieval, no Context Engine, just simple
full-text over versioned content.

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

#### Exposing it behind a single reverse proxy (`gateway`)

The `gateway` service (Caddy, `gateway/Caddyfile`) routes by prefix to each internal
API and exposes everything on a single port (`CEREBRO_GATEWAY_HOST_PORT`, default `8080`): `/memory`
→ `cerebro-memory-api`, `/docs` → `cerebro-docs-api`, `/flows` → `cerebro-flows-api`. The
idea is that if you're going to expose this behind your own Caddy/nginx (a VPS, whatever),
that external reverse proxy only needs **one** block pointing at the gateway's port,
instead of one block per service -- no matter how many modules the ecosystem has.
It doesn't replace each API's direct ports (they're still there for local dev or
direct access within the compose network); it's an additional, optional layer. With
`CEREBRO_MEMORY_URL=https://your-domain/memory` (same pattern for `_DOCS_`/`_FLOWS_`),
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

## Architecture

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

## `cerebro-memory` endpoints

| Method | Path | Scope | Description |
|---|---|---|---|
| `POST` | `/contexts` | write | create a context (`slug`, `name`, `kind`, `description?`) |
| `GET` | `/contexts` | read | list contexts (filtered to the token's `allowed_contexts`, if it has any) |
| `DELETE` | `/contexts/{slug}` | admin | deletes the context; 409 if it has memories unless `?force=true` (hard-deletes them along with the context, cascading to their `memory_edges`) |
| `POST` | `/memories` | write | create a memory; `context` required; rejects credentials (422) |
| `GET` | `/memories/search` | read | hybrid retrieval: `q`, `context?`, `scope?` (`auto`\|`all`\|`<slug>`, default `auto`), `type?`, `limit?`, `include_superseded?`, `expand?` (default `false`). Returns `{results, scope_decision, related}` -- see "Context Engine" and "Relationships and timeline" below. |
| `PATCH` | `/memories/{id}` | write | creates a new version + supersedes the previous one (never edits in place) |
| `DELETE` | `/memories/{id}` | write | `?hard=false` archives (default), `?hard=true` hard-deletes (cascading to its `memory_edges`) |
| `POST` | `/memories/{id}/edges` | write | creates an edge `{to_memory, relation, note?}`; 422 if `relation` is not in the vocabulary or `to_memory == id`, 404 if either memory doesn't exist, 409 if the edge already exists |
| `DELETE` | `/memories/{id}/edges/{edge_id}` | write | deletes an edge (must touch `id`) |
| `GET` | `/memories/{id}/related` | read | 1-hop neighbors (both directions) + virtual supersession chain; `relation?` filter |
| `GET` | `/timeline` | read | `episodic`/`decision` memories ordered by effective date; filters `context?`, `from?`, `to?`, `limit?=50` |
| `POST` | `/disambiguations/{id}/resolve` | write | resolves a pending disambiguation (`{"context": "<slug>"}`); grows `context_preferences` |
| `GET` | `/disambiguations/export` | admin | raw dataset for `cerebro memory export-disambiguations` |
| `GET` | `/stats` | read | memories by context/state (filtered by `allowed_contexts`), disambiguations (total/auto/agent/user), learned preferences |
| `POST` | `/tokens` | admin | creates a token with scopes (`{name, scopes, allowed_contexts?}`); the plaintext value is only returned in THIS response |
| `GET` | `/tokens` | admin | lists tokens (no hashes or plaintext values) |
| `DELETE` | `/tokens/{name}` | admin | revokes a token by name |
| `GET` | `/health` | none | no auth; checks the database connection |

The optional `X-Agent-Name` header identifies the calling agent (used in `source` and in
`audit_log`; default `"unknown"`) -- **except with a named token**, whose `name`
always overrides this header (see "Security": it's the agent's real identity, not
self-declared). Full detail on scopes and `allowed_contexts` in "Security" below.

## Security

Four pieces: tokens with their own identity, scopes, restriction by context (or
category in `cerebro-docs`), and backups. Everything except `GET /health` requires
`Authorization: Bearer <token>`, in both APIs.

### Tokens and scopes

Two valid credential types in the same header, in each API:

- **Root token** (`API_TOKEN` from each service's `.env`): compared byte by byte
  (`secrets.compare_digest`), it has all three scopes (`read`, `write`, `admin`) over
  all contexts/categories, unrestricted. Meant for yourself / initial bootstrap
  -- don't hand it out to individual agents. `cerebro-memory` and
  `cerebro-docs` each have their own `API_TOKEN` (see the "cerebro-docs" section of the
  `.env.example`) -- **it is not the same value by default**.
- **Named tokens**, created with `cerebro memory token create` (cerebro-memory
  only), `cerebro docs` doesn't yet have its own `token` subcommand -- use
  `POST /tokens` directly or the **cross-cutting** command below --, or the
  **cross-cutting** command `cerebro token create` (registers the same secret in both
  services at once, see below). They are stored as their SHA-256 hash -- **the plaintext
  value is shown only once, at creation time**, and cannot be recovered afterward (only
  revoked and a new one created).

```bash
# token scoped ONLY to cerebro-memory
cerebro memory token create claude-desktop --scopes read,write
cerebro memory token create agente-trabajo --scopes read --contexts cliente-acme,infraestructura
cerebro memory token list      # no hashes or plaintext values
cerebro memory token revoke agente-trabajo

# CROSS-CUTTING token: a single secret, registered in cerebro-memory AND cerebro-docs
cerebro token create claude-desktop --scopes read,write --contexts cliente-acme --categories infraestructura
cerebro token revoke claude-desktop
```

Three scopes (same vocabulary in both APIs):

| Scope | Covers in `cerebro-memory` | Covers in `cerebro-docs` |
|---|---|---|
| `read` | every `GET` (except `/health`) | every `GET` (except `/health`) |
| `write` | `POST`/`PATCH`/`DELETE` of memories, contexts (create), edges, and `POST /disambiguations/{id}/resolve` | `POST`/`PATCH`/`DELETE` of documents, categories (create/edit) |
| `admin` | token management (`/tokens/*`), `GET /disambiguations/export`, `DELETE /contexts/{slug}` | token management (`/tokens/*`), `DELETE /categories/{slug}` |

A token can have several scopes at once (`--scopes read,write`); `admin` does **not**
imply `read`/`write` automatically.

### Restriction by context / category

`--contexts a,b` (cerebro-memory) or `--categories a,b` (cerebro-docs) limits a token
to a subset; without the option, it sees all of them. In `cerebro-memory` it's applied in three
places (search, write, `/stats`/`/timeline` -- see detail below). In
`cerebro-docs`, `allowed_categories` silently filters `GET /categories`/`GET /documents` and
returns `403` on writes or direct reads (`GET
/documents/{categoria}/{slug}`) outside the list.

`cerebro-memory` detail (`allowed_contexts`):

- **Search** (`GET /memories/search`): an explicit `context`/`scope=<slug>` outside
  the list is `403`. Without an explicit context, `scope=all` silently narrows
  the results to the allowed subset, and `scope=auto` (Context Engine) directly
  **excludes** the disallowed contexts from scoring -- they can never win as auto-scope
  nor appear as an "ambiguous" candidate: the token isn't even told they exist.
- **Writes**: creating/updating/deleting a memory, edge, or disambiguation in a
  context outside the list is `403`.
- **`GET /stats` / `GET /timeline`**: rows for disallowed contexts are omitted rather
  than listed; an explicit `context` outside the list in `/timeline` is `403`.

### Cross-cutting tokens

`cerebro token create <name> --scopes ... [--contexts ...] [--categories ...]`
generates **a single secret** and registers it separately in both APIs (`POST /tokens`
of each, with `value` fixed to the same value). If one of the two calls fails, the
generated secret stays persisted locally
(`packages/cerebro-cli/src/cerebro_cli/tokens.py`) until both services
confirm success -- retrying the same command reuses the same secret instead of
generating a new one, and registration is idempotent by name in each API. `cerebro
token revoke <name>` revokes it in both services; a `404` in either (already revoked or
never existed there) counts as success.

### Agent identity

A named token's `name` **overrides** any `X-Agent-Name` the client
sends -- it ends up as `memory.source`/`documents.created_by` and as `audit_log.agent`
(cerebro-memory only) the real identity verified by the token, not whatever the
client itself claims to be. The root token has no fixed identity of its own, so it
still uses `X-Agent-Name` (default `"unknown"`), in both APIs.

### Secrets

`POST /memories` and `PATCH /memories/{id}` (**cerebro-memory only**) reject (422)
content that matches patterns of real credentials (AWS keys, GitHub/Slack
tokens, `sk-...`-style API keys, connection strings with an embedded password,
`password=...` assignments) -- see
`packages/cerebro-memory/src/cerebro_memory/security.py`. The rejection message
suggests the sanctioned reference format: `secret://<entorno>/<nombre>` (the value is never
stored). **`cerebro-docs` does NOT filter credentials** -- an explicit decision
(a runbook or an infra note sometimes needs to show an example connection
string or a placeholder), see `packages/cerebro-docs/src/cerebro_docs/api.py:8-11`.

### Strict input validation

`cerebro-docs` rejects (422) any unknown field in the body of its input
models (`StrictIn`, `extra="forbid"` -- see
`packages/cerebro-docs/src/cerebro_docs/api.py:53-61`): a client typo (e.g.
sending `content` instead of `body` in a section patch) never silently falls back to
the real field's default.

### Encryption and backups

TLS in transit (terminate it with a reverse proxy in front if you expose any API outside
your network); at rest, disk encryption at the VPS level as a baseline.

`cerebro backup` (`pg_dump` via `docker compose`) is the missing piece so that
"persistent memory" isn't an empty promise. A single shared Postgres means
a single dump covers **both** schemas (`cerebro_memory` and `cerebro_docs`) in one
operation. Automate it:

```bash
# Windows: Task Scheduler, daily at 3am
schtasks /create /tn "cerebro backup" /tr "D:\ruta\al\repo\.venv\Scripts\cerebro.exe backup" /sc daily /st 03:00

# Linux/Mac: cron, daily at 3am
0 3 * * * cd /ruta/al/repo && .venv/bin/cerebro backup >> backups/backup.log 2>&1
```

Actually test the restore every now and then (`cerebro restore <archivo.sql>`) -- a
backup that's never been verified doesn't count as a backup. `cerebro restore` overwrites **both**
schemas and asks for explicit confirmation (`--yes` to skip it in scripts).

## Context Engine (Phase 2, `cerebro-memory`)

`GET /memories/search` decides the search's *scope* before applying the final
retrieval. Three modes, via the `scope` parameter:

- **`scope=auto`** (default): runs the Context Engine. It's deterministic and cheap -- **no
  LLM calls** -- and decides in two steps:
  1. Preliminary unfiltered hybrid retrieval (top ~20) and sums the RRF score for each
     context, plus a boost from `context_preferences` (terms already associated with a
     context by earlier resolutions) and a boost if the query names the context
     explicitly.
  2. If the top-scoring context **dominates** (its normalized share exceeds
     `CONTEXT_ENGINE_DOMINANCE_THRESHOLD` *and* its margin over the second exceeds
     `CONTEXT_ENGINE_MARGIN_THRESHOLD`) -> `scope_decision.mode = "auto"`, the search
     already comes filtered to that context.
  3. If it doesn't dominate -> `scope_decision.mode = "ambiguous"`: `results` comes back
     **empty on purpose** (memories from different contexts are never mixed and returned
     blindly). Instead, `scope_decision.candidates` carries 2-4 possible contexts
     (slug, name, description, score) and `scope_decision.results_by_candidate` carries
     2-3 real results from each, as evidence for the caller to decide.
     `scope_decision.disambiguation_id` identifies the pending disambiguation.
- **`scope=all`**: no Context Engine, pure hybrid retrieval over all
  contexts -- Phase 1 behavior, used as the benchmark control.
- **`scope=<slug>`** (or passing `context=<slug>` directly): filters explicitly,
  without invoking the engine -- `scope_decision.mode = "explicit"`.

**Learning:** `POST /disambiguations/{id}/resolve {"context": "<slug>"}` records
the choice (`resolved_by='agent'`, or `'auto'` when the engine already dominated) and
grows `context_preferences`: the query's significant terms (normalized,
Spanish stopwords removed) add weight toward the chosen context. Similar questions in the
future lean toward that context -- and, with enough reinforcement, end up
resolving on their own in `auto` mode instead of being ambiguous again. The preference
boost is deliberately small per unit of weight
(`CONTEXT_ENGINE_PREFERENCE_BOOST_PER_WEIGHT`, default `0.008`): a single
generic term that legitimately collides between contexts (e.g. "month", "costs") shouldn't
be able to override the real retrieval signal from a single resolution; it takes
consistent reinforcement.

Thresholds configurable per environment (names `CONTEXT_ENGINE_*`, see
`packages/cerebro-memory/src/cerebro_memory/config.py` for the full list and the
defaults calibrated against `packages/cerebro-memory/evals/`).

`GET /stats` exposes `disambiguations` (total, how many were resolved `auto`, `agent`,
`user`, or `local_model` -- Phase 4, see below) and `preferences_learned` (terms
learned per context) -- it's the most direct way to see the system learning with
use; the MCP server exposes it as `memory_stats()`, and `cerebro memory stats` (CLI)
formats it for the console.

## Optional local classifier (Phase 4, `cerebro-memory`)

**OFF by default.** There's no point training or activating a local
disambiguation model while there is no real dataset of resolved ambiguities -- today none
exists. What this phase builds isn't the model, it's the **plug-in point**: the
`AmbiguityResolver` interface
(`packages/cerebro-memory/src/cerebro_memory/context_engine.py`) that `decide_scope`
invokes *after* Phase 2's deterministic scoring has already decided a case is
ambiguous, to try to resolve it locally instead of returning it to the calling agent.

Two implementations:

- **`NullResolver`** (default, `CONTEXT_ENGINE_RESOLVER` unset or `"none"`):
  always returns `None` -- today's flow (ambiguity to the agent, Phase 2) stays
  **exactly the same**, byte for byte, as long as this isn't activated on purpose.
- **`OllamaResolver`** (`CONTEXT_ENGINE_RESOLVER=ollama`): calls the Ollama API
  (`POST {OLLAMA_URL}/api/generate`, default `http://localhost:11434`, model
  `OLLAMA_MODEL`, default `qwen2.5:1.5b`) with a short prompt listing the
  candidates (slug + description) and asking for a slug as the answer. 2s timeout.
  Any failure -- Ollama isn't running, timeout, unparseable response, or a
  slug not among the candidates -- silently falls back to `None` (same
  behavior as `NullResolver`): **this must never be able to break a search**.
  It doesn't install or configure Ollama for you; it only brings the client with this fallback.

When the resolver does return a valid slug, the ambiguity is resolved as if
the Context Engine itself had dominated from the start (`scope_decision.mode ==
"auto"`, results already filtered to that context), but it's recorded in
`disambiguation_log` with `resolved_by = 'local_model'` -- distinguishable in `GET /stats`
/ `cerebro memory stats` from `auto` resolutions (deterministic scoring) and `agent`
resolutions (agent/MCP choosing with the conversation's context).

**When to actually enable it**: (a) there are **≥ ~500 recorded disambiguations** -- use
`cerebro memory export-disambiguations` to see how many there are and export the dataset --
**and** (b) there's a measured reason to do it (latency, cost, or a strict
privacy policy of "not even the query leaves the VPS"). Without both conditions, this is
infrastructure left unused on purpose -- earn your complexity.

Environment variables (`packages/cerebro-memory/src/cerebro_memory/config.py`):

| Variable | Default | Use |
|---|---|---|
| `CONTEXT_ENGINE_RESOLVER` | `none` | `none` (NullResolver) \| `ollama` (OllamaResolver) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | model to ask Ollama for |

## Relationships and timeline (Phase 3, `cerebro-memory`)

Lightweight graph **in Postgres** (`memory_edges`,
`packages/cerebro-memory/db/migrations/003_edges.sql`) -- no dedicated graph
database. Two pieces: explicit edges between memories, and a timeline over
`occurred_at`.

**Relationship vocabulary** (controlled, `CHECK` on the table -- not free text):
`relates_to` (generic association), `caused_by` (`from` was caused by `to` --
decision → its cause), `part_of` (`from` is part of `to` -- procedure → project),
`contradicts` (`from` contradicts `to`), `follows` (`from` occurred after / as a
consequence of `to` -- episode → its consequence).

```bash
# Create an edge: the "switch provider" decision was caused by "price went up"
curl -s -X POST localhost:8000/memories/$DECISION_ID/edges \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"to_memory":"'$CAUSA_ID'","relation":"caused_by","note":"motivo de la decision"}'

# 1-hop neighbors (both directions), optionally filtered by relation
curl -s "localhost:8000/memories/$DECISION_ID/related" -H "Authorization: Bearer $TOKEN"
curl -s "localhost:8000/memories/$DECISION_ID/related?relation=caused_by" -H "Authorization: Bearer $TOKEN"

# Delete an edge
curl -s -X DELETE localhost:8000/memories/$DECISION_ID/edges/$EDGE_ID -H "Authorization: Bearer $TOKEN"
```

`GET /memories/{id}/related` returns, for each neighbor, `relation`, `direction`
(`"outgoing"` if `id` is the relation's origin, `"incoming"` if it's the destination),
`note`, `created_by`, and the full neighboring memory. Additionally, **automatically**,
it includes the supersession chain (`memories.superseded_by`, from Phase 1) as a
**virtual** `"supersedes"` relation (`virtual: true`, `edge_id: null`) -- it's never
written to `memory_edges`, it's derived on read. `relation=supersedes` as a filter
returns only that chain; any other value from the vocabulary filters only real edges.

Hard-deleting a memory (`DELETE /memories/{id}?hard=true`) cascades to
(`ON DELETE CASCADE`) all its edges, in both directions -- no orphaned edges are
left behind.

### Retrieval expansion to 1 hop (`expand=true`)

`GET /memories/search?expand=true` adds, in addition to `results`, a **separate**
`related` block with the direct neighbors of the top 3 results (deduplicated, max 5,
`status=active` only). **`related` is never mixed with `results`** -- it must not alter
retrieval metrics (precision/recall/contamination from `evals/`, see below).

Cross-context contamination rule: if the search already resolved a single context (explicit
`scope` or `auto` with a clear context), a neighbor from **another** context is only
included if the edge is **explicit** (`virtual: false`) -- it's marked `cross_context:
true`. The logic is that an explicit edge is an intentional bridge the
user/agent created on purpose with `POST /memories/{id}/edges`, not an accidental
vocabulary collision between contexts.

```bash
curl -s "localhost:8000/memories/search?q=por+que+migramos+de+proveedor&context=infraestructura&expand=true" \
  -H "Authorization: Bearer $TOKEN"
```

### Timeline

`GET /timeline` gathers `episodic` and `decision` memories (the ones that make sense in a
timeline), ordered by effective date (`occurred_at`, or `created_at` if none was
specified) -- meant to answer "what happened with X over the last few weeks?".

```bash
curl -s "localhost:8000/timeline?context=infraestructura&from=2026-07-01T00:00:00Z&to=2026-07-31T00:00:00Z&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

`add_edge`/`delete_edge` are recorded in `audit_log` (actions `add_edge` /
`delete_edge`), same as the rest of the write operations.

## `cerebro-docs`: versioned Markdown documents

Sibling service to `cerebro-memory`, for the other end of the spectrum: not short,
atomic memories, but complete Markdown documents (runbooks, specifications,
long-form notes) organized into categories, with version history and the ability
to patch a specific section without resending the entire document.

| Method | Path | Scope | Description |
|---|---|---|---|
| `POST` | `/categories` | write | creates a category (`slug`, `name`, `description?`, `hidden?=false`, `locked?=false`) |
| `GET` | `/categories` | read | lists categories with `hidden=false` (also filtered to the token's `allowed_categories`, if it has any) -- a `hidden` category is still reachable if you know its exact slug |
| `PATCH` | `/categories/{slug}` | write | renames/edits a category (including `hidden`); its documents don't change logical path (the FK is `category_id`, not copied text). If the slug changes, it registers a redirect for every document in the category. 409 if the category is `locked` and `hidden=false` is attempted -- a `locked` category can never be revealed |
| `DELETE` | `/categories/{slug}` | admin | 409 if it has documents, unless `?force=true` (cascades to documents and their version history) |
| `POST` | `/documents` | write | creates a document (`title`, `content`, `category`, `slug?`); 409 if the slug already exists in that category |
| `GET` | `/documents/{category}/{slug}` | read | reads a document by its exact path (works for archived and `hidden` categories alike). If there's no direct match, it tries `slug_redirects`; if found, responds with the current document and `redirected_from: {category, slug}` |
| `GET` | `/documents` | read | lists documents with `status=active`, `updated_at desc`; filters `category?`, `q?` (simple full-text), `limit?=20`, `offset?=0`. Without an explicit `category`, it also excludes `hidden` categories |
| `GET` | `/documents/archived` | read | same as above but `status=archived` -- a dedicated listing, never mixed with the normal one |
| `GET` | `/documents/{id}/versions` | read | full `document_versions` history (most recent first, with the full `content`) -- read only, no automatic restore |
| `PATCH` | `/documents/{id}` | write | full replacement (including moving between categories); snapshots the previous version in `document_versions` before writing. If `slug`/`category` change, it registers a redirect |
| `PATCH` | `/documents/{id}/section` | write | partial patch by heading: `operation` one of `replace`\|`append`\|`insert_after`\|`insert_before`\|`delete`; snapshots the same way as a full replacement |
| `POST` | `/documents/{id}/archive` | write | archives (soft-delete): disappears from `/documents`, stays reachable by exact path, reversible |
| `POST` | `/documents/{id}/unarchive` | write | reverts an archive |
| `DELETE` | `/documents/{id}` | write | deletes the document (cascading to `document_versions`) -- irreversible, unlike archive |
| `POST` | `/tokens` | admin | creates a token with scopes (`{name, scopes, allowed_categories?}`) |
| `GET` | `/tokens` | admin | lists tokens |
| `DELETE` | `/tokens/{name}` | admin | revokes a token by name |
| `GET` | `/stats` | read | counts of categories/documents/versions (minimal mirror of cerebro-memory's `/stats`, without disambiguations or preferences) |
| `GET` | `/health` | none | no auth; checks the database connection |

**Hidden and locked categories** (`hidden`/`locked`): `hidden=true` removes the
category from `GET /categories`/`GET /documents` without an explicit filter -- it's still
reachable by creating/reading documents with its exact slug. It's toggleable via `PATCH
/categories/{slug}`. `locked=true` (only settable at creation, requires `hidden=true`)
hides it FOREVER -- no endpoint allows reverting it afterward. Meant for
internal reference categories that should never be browsable (e.g. the supporting `.md` files
for a future `cerebro-flows` module).

**Archiving**: a document's `status` is `active` or `archived`. Archiving (soft-delete,
reversible) is the alternative to `DELETE /documents/{id}` (irreversible) when you
want to take something out of circulation without losing it.

**Slug redirects**: renaming a document or its category leaves a record in
`slug_redirects` (old coordinate `(category, slug)` -> current `document_id`, never
chained). `GET /documents/{category}/{slug}` falls back to that record only if there's no
direct match -- a real document at that path always wins. The MCP tool `docs_get`
uses this to alert the model and get it to stop referencing the old path.

**A section is from one heading to the next of the same or higher level.** If the
searched heading appears more than once, `PATCH /documents/{id}/section` returns `409`
(ambiguous, it never guesses which one); if it doesn't exist, `404` unless
`create_if_missing=true` is passed (creates the section at the end, with `new_heading_level`, default
`2`).

```bash
curl -s -X PATCH localhost:8010/documents/$DOC_ID/section \
  -H "Authorization: Bearer $DOCS_TOKEN" -H "Content-Type: application/json" \
  -d '{"heading":"## Pasos","operation":"append","body":"4. Verificar healthcheck"}'
```

`cerebro-docs` **does not filter credentials** in content (unlike
`POST /memories` in cerebro-memory) and **rejects unknown fields** in any
request body (`422`, see "Strict input validation" above). It has no
semantic retrieval or Context Engine -- search (`GET /documents?q=...`) is
simple full-text (`websearch_to_tsquery('simple', ...)` + `ts_rank`), always
parameterized.

## `cerebro-flows`: traffic-light-style flow engine

A third type of content besides memory (distilled facts) and documents
(full Markdown): processes with steps, decisions, and approval checkpoints,
defined in YAML. The model **never** receives the full definition -- the server
reveals one step at a time, so "shouldn't skip a checkpoint" becomes "can't
see the next step until it's approved." It's an information and sequence-control
service ("traffic light"), not an orchestrator: whoever executes the real actions (Jira,
SSH, whatever) is still the model, with its own tools.

Two stores with different roles: **Postgres** (schema `cerebro_flows`) holds everything
immutable -- versioned definitions, each run's event history
(`flow_run_events`, written incrementally). **Redis** holds ONLY the mutable
pointer of an in-progress run (`flow_run:<run_id>` -- which step is current, sliding TTL
`FLOW_RUN_TTL_HOURS`, default 72h) -- the definition is re-read and re-parsed from
Postgres at every step, never fully cached.

| Method | Path | Scope | Description |
|---|---|---|---|
| `POST` | `/categories` | write | creates a category (`slug`, `code`, `name`, `description?`) -- `code` is the prefix for its flows' ids (e.g. category `incident`/`INC` → flows `INC-1`, `INC-2`...) |
| `GET` | `/categories` | read | lists categories |
| `POST` | `/flows/validate` | write | validates a YAML WITHOUT saving it -- a precise error (which step, which field) for iterative authoring |
| `POST` | `/flows` | write | creates a definition (`category`, `yaml_content`, `code?`); `code` auto-generated if omitted |
| `GET` | `/flows/{code}` | read | full definition (YAML of the current version) |
| `GET` | `/flows` | read | lists definitions (`category?`) |
| `PATCH` | `/flows/{code}` | write | replaces the YAML (new version, snapshots the previous one) -- a run in progress keeps following the version it started with |
| `DELETE` | `/flows/{code}` | write | deletes (cascading to versions and runs) |
| `POST` | `/flows/{code}/start` | write | starts a run -- `{run_id, status, step}` |
| `POST` | `/runs/{run_id}/next` | write | advances to the next step; body `{decision?}` if the current step is a decision |
| `POST` | `/runs/{run_id}/approve-checkpoint` | write | approves the current step's checkpoint -- unlocks the next `next` |
| `POST` | `/runs/{run_id}/reject-checkpoint` | write | rejects the checkpoint -- the flow jumps to the YAML's `checkpoint.on_reject` |
| `POST` | `/runs/{run_id}/abort` | write | aborts a run in progress (irreversible) |
| `GET` | `/runs/{run_id}` | read | current state of a run |

**First step is always synthetic**: if the definition declares `tools:` (the tools the
model is going to need), `flow_start` first returns a `__prerequisites__` step with
`tools_required` -- the check itself is done by the model (trying `ToolSearch`, flagging
if something is actually missing), the server just forces it to appear first in the
protocol. A `decision`-type step is not inferred: the model reports the evaluated condition
(`decision` in the `/next` body) against the YAML's `branches`.

```bash
curl -s -X POST localhost:8007/flows/incident/start \
  -H "Authorization: Bearer $FLOWS_TOKEN"
# {"run_id": "...", "status": "in_progress", "step": {"id": "__prerequisites__", ...}}

curl -s -X POST localhost:8007/runs/$RUN_ID/next -H "Authorization: Bearer $FLOWS_TOKEN" -d '{}'
curl -s -X POST localhost:8007/runs/$RUN_ID/next -H "Authorization: Bearer $FLOWS_TOKEN" \
  -d '{"decision": "sufficient"}'
```

## Tests

```bash
# each package has its own suite (testpaths = ["tests"] in its pyproject.toml)
cd packages/cerebro-memory && pytest
cd packages/cerebro-docs && pytest
cd packages/cerebro-flows && pytest
cd packages/cerebro-clients && pytest
cd packages/cerebro-mcp && pytest
cd packages/cerebro-cli && pytest
```

In `cerebro-memory`: `tests/test_rrf.py`, `tests/test_security.py`,
`tests/test_context_engine.py`, the unit part of `tests/test_auth.py`
(`Principal`, `hash_token`/`generate_token`), and the unit part of
`tests/test_graph.py` (relationship vocabulary) are unit tests (no database).
`tests/test_supersedence.py`, the integration part of `tests/test_auth.py` (token
lifecycle, scope enforcement and `allowed_contexts` enforcement, `DELETE
/contexts/{slug}`), and the integration part of `tests/test_graph.py` (edges,
no-duplicates, direction in `related`, hard-delete cascade, `timeline`
ordering) are skipped automatically if `DATABASE_URL` isn't reachable (run
`docker compose up -d` first from the repo root).

In `cerebro-docs`: `tests/test_auth.py`, `tests/test_documents.py`,
`tests/test_sections.py`, `tests/test_slug_redirects.py`, `tests/test_slugs.py`,
`tests/test_strict_input.py` -- same criterion, the integration part needs
`DATABASE_URL` reachable.

In `cerebro-flows`: `tests/test_schema.py` is a unit test (YAML parsing/referential
validation, no DB); `tests/test_auth.py` and `tests/test_flows.py` (CRUD + the full
execution engine, including an end-to-end run through the example flow `INC-22` from the
design document) also need `REDIS_URL` reachable, not just Postgres.

Packages with an integration suite (`cerebro-memory`, `cerebro-docs`,
`cerebro-flows`, `cerebro-cli`, and transitively `cerebro-clients`) isolate their tests
against an ephemeral `cerebro_test` database (dropped/recreated in each package's
`pytest_configure`, before any test module is imported) -- they never write against
the real development database. See `packages/cerebro-memory/tests/conftest.py` for the
mechanism's detail, and `packages/cerebro-flows/tests/conftest.py` for its variant
(it also runs `FLUSHDB` on a dedicated Redis database, `/15`).

## Connecting to Claude (MCP server: `cerebro-mcp`)

`packages/cerebro-mcp/src/cerebro_mcp/server.py` exposes **all three** services as a
single stdio MCP server (official `mcp` SDK, `FastMCP`). It's a thin adapter:
each tool calls the corresponding HTTP API via `cerebro_clients` (`MemoryClient` /
`DocsClient` / `FlowsClient`), with no business logic of its own -- all of it lives in the APIs,
so `cerebro-cli` shares exactly the same path.

36 tools available:

- **`memory_*`** (10, talk to `cerebro-memory`): `memory_search`,
  `memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`,
  `memory_create_context`, `memory_stats`, `memory_link`, `memory_related`,
  `memory_timeline`.
- **`docs_*`** (13, talk to `cerebro-docs`): `docs_create_category`,
  `docs_categories`, `docs_save`, `docs_get`, `docs_search`, `docs_list`,
  `docs_update`, `docs_patch_section`, `docs_delete`, `docs_archive`,
  `docs_unarchive`, `docs_list_archived`, `docs_history`.
- **`flow_*`** (13, talk to `cerebro-flows`): `flow_create_category`,
  `flow_categories`, `flow_validate`, `flow_save`, `flow_get`, `flow_list`,
  `flow_update`, `flow_delete` (authoring/CRUD) + `flow_start`, `flow_next`,
  `flow_approve_checkpoint`, `flow_reject_checkpoint`, `flow_abort` (execution
  engine -- see "`cerebro-flows`" above for the step-by-step protocol).

`memory_search` uses `scope=auto` by default (Context Engine). If the response is
ambiguous, `message` carries text already formatted for deciding or displaying to the user, and
`candidates`/`results_by_candidate` the raw evidence. The server remembers, in
process memory (a single slot, not history), the `disambiguation_id` of the
last ambiguous search; if the NEXT call to `memory_search` passes an explicit
`context`, it assumes that's how that ambiguity was resolved and automatically calls
`POST /disambiguations/{id}/resolve` -- without the agent having to do it by hand.
That feeds `context_preferences`, so similar questions tend to resolve
themselves next time. `memory_stats()` exposes counts of memories, disambiguations
(auto vs agent), and learned preferences -- useful for seeing the learning in action.
`memory_search` also accepts `expand=True` (Phase 3, default `False`) to receive a
`related` block with the direct neighbors of the results -- see "Relationships and
timeline" above.

`memory_link(from_memory_id, to_memory_id, relation, note?)` creates an explicit edge
between two memories (vocabulary: `relates_to`, `caused_by`, `part_of`, `contradicts`,
`follows`); its docstring explains when to use each one (decisions→causes,
procedures→projects, episodes→consequences). `memory_related(memory_id,
relation?)` lists 1-hop neighbors, including the virtual supersession chain.
`memory_timeline(context?, from_date?, to_date?, limit?)` answers questions like
"what happened with X over the last few weeks?".

`docs_save(category, title, content, slug?)` creates a new document.
`docs_patch_section(document_id, heading, operation, body?, create_if_missing?,
new_heading_level?)` patches a specific section without resending the entire document --
the intended use is for an agent to update, e.g., a runbook line by line instead
of rewriting it whole every time. `docs_search(query, category?, limit?, offset?)` does
simple full-text search; `docs_list`/`docs_categories` list without a query (neither
includes archived documents or hidden categories).

`docs_create_category(slug, name, description?, hidden?, locked?)` accepts `hidden`
for categories that shouldn't appear in listings without an exact slug (e.g. internal
reference for a module), and `locked` (requires `hidden`) for ones that should never be
revealed. `docs_archive(document_id)`/`docs_unarchive(document_id)` are the
`cerebro-docs` equivalent of `memory_forget` (reversible soft-delete, preferable to
`docs_delete` when you don't want to lose the content); `docs_list_archived` lists
archived ones. `docs_history(document_id)` reads the `document_versions` history
(read only, no automatic restore). If `docs_get` resolves a path that was
renamed, the response carries an `alert` asking the model to stop using the
old path and fix it anywhere it had it saved.

After installing `cerebro-mcp` (`pip install -e packages/cerebro-mcp`) the console
entry point `cerebro-mcp` becomes available (see `[project.scripts]` in its
`pyproject.toml`). It requires **both** APIs to be running
(`python -m cerebro_memory.main` and `python -m cerebro_docs.main`, or the equivalent in
Docker).

Environment variables the MCP server reads (via `cerebro_clients.config`):

| Variable | Default | Use |
|---|---|---|
| `CEREBRO_MEMORY_URL` | `http://localhost:8005` | `cerebro-memory` base URL |
| `CEREBRO_DOCS_URL` | `http://localhost:8010` | `cerebro-docs` base URL |
| `CEREBRO_FLOWS_URL` | `http://localhost:8020` | `cerebro-flows` base URL |
| `CEREBRO_TOKEN` | *(empty)* | shared token for the three APIs |
| `CEREBRO_AGENT_NAME` | `cerebro-client` | identity sent as `X-Agent-Name` (audit log, `memory.source`/`documents.created_by`) |
| `KNOWLEDGEOS_API_URL` / `KNOWLEDGEOS_API_TOKEN` / `KNOWLEDGEOS_AGENT_NAME` | *(fallback)* | legacy, **applies only to `cerebro-memory`**; if you already had these set from before the migration they keep working |

Note on defaults: `CEREBRO_MEMORY_URL` defaults to assuming the Docker port
(`8005`), while `CEREBRO_DOCS_URL` defaults to assuming the local dev-without-Docker
port (`8010`, not `8006`). If you run both APIs in the same mode (A or B),
export both variables explicitly so they point to the same side -- see
"Quickstart" above for each mode's port pairs.

### Claude Code

```bash
claude mcp add cerebro --scope user \
  -e CEREBRO_MEMORY_URL=http://localhost:8005 \
  -e CEREBRO_DOCS_URL=http://localhost:8006 \
  -e CEREBRO_FLOWS_URL=http://localhost:8007 \
  -e CEREBRO_TOKEN=change-me-dev-token \
  -e CEREBRO_AGENT_NAME=claude-code \
  -- cerebro-mcp
```

### Claude Desktop

Add this to `claude_desktop_config.json` (menu Claude > Settings > Developer > Edit
Config):

```json
{
  "mcpServers": {
    "cerebro": {
      "command": "D:\\dev\\jobs\\luisjdev\\cerebro\\.venv\\Scripts\\cerebro-mcp.exe",
      "env": {
        "CEREBRO_MEMORY_URL": "http://localhost:8005",
        "CEREBRO_DOCS_URL": "http://localhost:8006",
        "CEREBRO_FLOWS_URL": "http://localhost:8007",
        "CEREBRO_TOKEN": "change-me-dev-token",
        "CEREBRO_AGENT_NAME": "claude-desktop"
      }
    }
  }
}
```

If `cerebro-mcp` isn't on the `PATH` that Claude Desktop sees, use the absolute path to
the venv's executable, e.g. on Windows:
`"command": "D:\\ruta\\al\\repo\\.venv\\Scripts\\cerebro-mcp.exe"`.

## CLI (`cerebro`)

`packages/cerebro-cli/src/cerebro_cli/main.py` (console entry point `cerebro`,
installed by `pip install -e packages/cerebro-cli`) is a thin client of both
HTTP APIs via `cerebro_clients` -- same as the MCP server, it has no business
logic of its own (except for orchestrating the Markdown importer, inherited from
`cerebro_memory`, and handling partial failure of cross-cutting tokens, see
"Security" above). Before dispatching any subcommand, `main()` loads
`.env.production`/`.env` from the monorepo root without overriding variables already present in
the environment (`packages/cerebro-cli/src/cerebro_cli/dotenv.py`) -- so `cerebro memory
stats` still talks to the production VPS by default if that file points there,
without depending on a hand-made shell wrapper.

```bash
cerebro --help
```

Three groups of subcommands: `cerebro memory ...`, `cerebro docs ...`, and
cross-cutting commands with no prefix.

### `cerebro memory ...`

```bash
cerebro memory stats                                              # same as GET /stats from cerebro-memory
cerebro memory export-disambiguations --output disambiguations.jsonl
cerebro memory export-disambiguations --resolved-only

# tokens SCOPED only to cerebro-memory - requires admin auth
cerebro memory token create claude-desktop --scopes read,write
cerebro memory token create agente-trabajo --scopes read --contexts cliente-acme,infraestructura
cerebro memory token list
cerebro memory token revoke agente-trabajo
```

`export-disambiguations` always prints how many examples exist against the plan's
threshold (`~500`, see "Optional local classifier" above) so it's easy to know if it's already
worth considering fine-tuning.

#### Importing existing memories (Phase 5)

`cerebro memory import-markdown` is the **first Phase 5 connector**: it imports
existing memory Markdown files (`MEMORY.md`/`CLAUDE.md` in Claude Code
style, or loose notes) as `cerebro-memory` memories. It was chosen as connector #1
on purpose because it solves the migration from the user's status quo, not because it's
the most technically interesting.

The parsing (`packages/cerebro-memory/src/cerebro_memory/markdown_importer.py`, a
pure parser that `cerebro-cli` reuses without depending on the original
`cli.py`/`mcp_server.py` -- already removed from `cerebro-memory`) recognizes three formats, in this
order:

1. **Claude Code-style memory YAML frontmatter** (`name`, `description`,
   `metadata.type`) -> one memory per file. `description` is used as the title,
   the body (without the frontmatter) as the content. `metadata.type` mapping:
   `user`/`feedback`/`reference` -> `semantic`; `project` -> `semantic` with
   `importance=0.7`.
2. **`MEMORY.md` index** (lines `- [title](file.md) — hook`): if the
   linked file exists, the link is followed and parsed recursively (with the same
   dispatch: it can in turn have frontmatter); if it doesn't exist, the bullet itself
   becomes a small memory (`title`, `content=hook`).
3. **Generic Markdown** (fallback): split by level 1-2 headings; each
   section with >= 2 lines of real content becomes a memory (`title`=heading,
   `content`=body); smaller sections merge with the previous one.

In any of the three cases, code blocks longer than 30 lines are truncated to
`[código truncado]` before processing -- a memory is a distilled summary, not a
source-code dump.

```bash
# preview: what would be imported, without writing anything
cerebro memory import-markdown ./mis-notas --context notas-personales --dry-run

# real import; creates the context if it doesn't exist
cerebro memory import-markdown ./mis-notas \
  --context notas-personales --create-context \
  --context-description "Notas migradas desde Markdown"

# a single file, forced type
cerebro memory import-markdown ./MEMORY.md --context notas-personales --type semantic
```

Before inserting each memory, the importer searches by similarity (`GET
/memories/search` scoped to the target context) using the content itself as the query;
if the top result has a high RRF score **and** the exact same title, it skips it
and reports it as "duplicate" instead of reinserting it -- so a second run over
the same directory (or a `MEMORY.md` that links files the recursive glob already
walked separately) doesn't duplicate memories. Credentials detected by the API
(`POST /memories` -> 422) are caught and reported as "rejected" without interrupting the
rest of the import. At the end it prints a summary: `N importadas, M duplicadas
(saltadas), K rechazadas` (`N imported, M duplicates (skipped), K rejected`).

### `cerebro docs ...`

```bash
cerebro docs category create infraestructura --name "Infraestructura" --description "Runbooks y notas de infra"
cerebro docs category list
cerebro docs category rename infraestructura infra --name "Infra"
cerebro docs category delete infra --force

cerebro docs save infraestructura "Runbook: restore de Postgres" --content-file runbook.md
cerebro docs get infraestructura runbook-restore-de-postgres
cerebro docs list --category infraestructura --limit 10
cerebro docs search "restore postgres"

cerebro docs update $DOC_ID "Runbook: restore de Postgres (v2)" infraestructura --content-file runbook-v2.md
cerebro docs patch-section $DOC_ID "## Pasos" append --body "4. Verificar healthcheck" --create-if-missing

cerebro docs archive $DOC_ID           # soft-delete, reversible
cerebro docs unarchive $DOC_ID
cerebro docs list --archived           # lists what's archived
cerebro docs history $DOC_ID           # document_versions history

cerebro docs delete $DOC_ID --yes
cerebro docs stats

# hidden category (WIP) or locked forever (internal reference)
cerebro docs category create referencia-interna --hidden
cerebro docs category create refs-flows --hidden --locked
cerebro docs category rename referencia-interna referencia-interna --visible   # reveals it (fails if --locked)

# bulk importer (not distilled -- each file is saved whole)
cerebro docs import-markdown ./runbooks --category infraestructura --dry-run
cerebro docs import-markdown ./runbooks --category infraestructura --update
```

`--content-file` is optional in `save`/`update` -- if omitted, the CLI reads
content from stdin (useful for piping the output of another command or a heredoc). The
bulk importer (`import-markdown`) is `cerebro-docs`'s equivalent of `cerebro
memory import-markdown`, but without distilling: each `.md` file is saved as a
complete document (title = the file's first `# heading` or the filename,
slug = sanitized filename). By default it skips files whose `(categoria, slug)`
already exists (`--update` updates them instead of skipping).

### `cerebro flow ...`

Definition CRUD only -- **no commands to execute a flow**
(`flow_start`/`flow_next` don't make sense typed by hand; a flow is driven by a
model turn by turn via the `flow_*` MCP tools).

```bash
cerebro flow category create incident INC --name Incidencias
cerebro flow category list

cerebro flow validate --yaml-file incidencia.yaml
cerebro flow save incident --yaml-file incidencia.yaml
cerebro flow get INC-1
cerebro flow list --category incident
cerebro flow update INC-1 --yaml-file incidencia-v2.yaml
cerebro flow delete INC-1 --yes
cerebro flow stats
```

### Cross-cutting commands (no prefix)

```bash
# backup / restore (pg_dump / psql via docker compose) - covers BOTH schemas
cerebro backup --output backups/
cerebro restore backups/cerebro-20260812-030000.sql   # asks for confirmation (DESTRUCTIVE)
cerebro restore backups/cerebro-20260812-030000.sql --yes   # without confirming

# CROSS-CUTTING tokens (one secret, registered in cerebro-memory and cerebro-docs)
cerebro token create claude-desktop --scopes read,write --contexts cliente-acme --categories infraestructura
cerebro token revoke claude-desktop
```

`cerebro token create` prints the plaintext token **only once** -- save it
right away (e.g. as the corresponding MCP client's `CEREBRO_TOKEN`). See "Cross-cutting
tokens" in the "Security" section above for the behavior on
partial failure.

## Evaluation

`cerebro-memory`'s retrieval evaluation suite
(`packages/cerebro-memory/evals/`, see `packages/cerebro-memory/evals/README.md` for
the full detail on metrics and corpus) measures precision@k, recall@k, and cross-context
contamination rate, with a synthetic corpus of ~40 memories across 6 contexts
and 30 test cases in Spanish. `cerebro-docs` has no equivalent evaluation
suite (it does no semantic retrieval or scoping, just simple full-text).

```bash
cd packages/cerebro-memory

# baseline: keyword overlap, no notion of context
python evals/harness/run_eval.py --adapter naive

# real cerebro-memory, via the HTTP API (requires the API running and Postgres up)
python -m cerebro_memory.main &   # or in another terminal

# control (Phase 1): hybrid retrieval without Context Engine
KNOWLEDGEOS_SEARCH_SCOPE=all python evals/harness/run_eval.py --adapter cerebro-memory --include-superseded

# Context Engine (Phase 2): scope=auto
KNOWLEDGEOS_SEARCH_SCOPE=auto python evals/harness/run_eval.py --adapter cerebro-memory --include-superseded
```

(the environment variable keeps its legacy name `KNOWLEDGEOS_SEARCH_SCOPE` -- the
`evals/` harness wasn't touched in the monorepo migration, it was only moved.)

`evals/harness/adapters/` talks to the real API over HTTP (same as the
MCP server would): in `setup()` it checks `/health`, creates any missing corpus
contexts, and purges memories from previous runs.

The corpus's 3 `superseded`→`active` pairs (`evals/memories.yaml`,
`superseded_by_id`) are inserted as a real supersession chain when
`--include-superseded` is used: `POST` the old version, `PATCH` with the new
one's content -- the same path that `memory_update()` would produce in production, instead
of inserting both as independent active rows.

**Latest measured calibration** (k=5, `--include-superseded`, `evals/` corpus):

| Mode | Category | Precision@5 | Recall@5 | Contamination |
|---|---|---|---|---|
| `scope=all` (control) | ambiguous | 20% | 100% | 25% |
| `scope=all` (control) | direct | 20% | 100% | 0% |
| `scope=all` (control) | temporal | 20% | 100% | 0% |
| `scope=auto` (Context Engine) | ambiguous | 20% | 100% | **0%** |
| `scope=auto` (Context Engine) | direct | 20% | 100% | 0% |
| `scope=auto` (Context Engine) | temporal | 20% | 100% | 0% |

Thresholds calibrated in `packages/cerebro-memory/src/cerebro_memory/config.py`
(`CONTEXT_ENGINE_*`); between benchmark runs, it truncates `disambiguation_log` and
`context_preferences` to measure `scope=auto` cold (without accumulated learning from
a previous run).

Read `packages/cerebro-memory/evals/README.md` for how to add your own cases or
corpus, and `--include-superseded` so the `temporal` category is meaningful.

These numbers come from before the monorepo migration and the separation of
`cerebro-docs`; neither change touches `cerebro-memory/retrieval.py`,
`context_engine.py`, or the `evals/` corpus, so they stand as the baseline
until the next recalibration.

## Structure

```
compose.yaml                  # postgres (always) + cerebro-memory-api + cerebro-docs-api (profile "full")
.env.example                  # shared variables: DATABASE_URL, API_TOKEN, APP_PORT, EMBEDDING_*, CONTEXT_ENGINE_*, cerebro-docs section
.env.production                # (not versioned) production config that cerebro-cli loads automatically
docs/
    technical-manual.md          # architecture, per-package reference, API tables, auth model
    user-manual.md                # day-to-day usage: memory/docs/flows, the CLI, tokens, FAQ
packages/
    cerebro-memory/            # pure API service: persistent memory
        Dockerfile              # multi-stage image, non-root, pre-downloads the model at build time
        pyproject.toml          # no [project.scripts]: exposes no CLI or MCP of its own
        db/migrations/           # 001_init .. 005_schema_cerebro_memory
        evals/                    # retrieval evaluation suite (see "Evaluation")
        src/cerebro_memory/
            config.py             # settings from env, includes CONTEXT_ENGINE_*
            db.py                 # asyncpg pool + migration application on startup
            embeddings.py         # EmbeddingProvider (local fastembed)
            security.py           # credential detection in remember()
            auth.py                # Principal, scopes, token hashing, api_tokens CRUD
            retrieval.py           # hybrid search (vector + full-text) fused with RRF
            context_engine.py      # Context Engine + AmbiguityResolver/NullResolver/OllamaResolver
            graph.py                # edges (memory_edges), related() 1-hop, timeline, search expand
            api.py                  # FastAPI app (auth, scopes, CRUD, search, disambiguations, stats, edges, timeline, tokens)
            markdown_importer.py    # pure parsing, reused by cerebro-cli (Phase 5)
            main.py                  # uvicorn entrypoint
        tests/
    cerebro-docs/               # pure API service: versioned Markdown documents
        Dockerfile
        pyproject.toml            # [project.scripts]: cerebro-docs (uvicorn entrypoint, no user-facing CLI)
        db/migrations/001_init.sql
        src/cerebro_docs/
            config.py               # minimal mirror of cerebro_memory.config, no embeddings/Context Engine
            db.py / auth.py
            slugs.py                 # slugify() for documents/categories
            sections.py               # apply_section_patch(): replace/append/insert_after/insert_before/delete
            api.py                     # FastAPI app: categories, versioned documents, tokens, stats
            main.py                     # uvicorn entrypoint
        tests/
    cerebro-clients/             # shared httpx SDK, no entry points (library)
        src/cerebro_clients/
            base.py                   # exceptions + base HTTP client
            config.py                  # resolution of CEREBRO_MEMORY_URL/CEREBRO_DOCS_URL/CEREBRO_TOKEN/CEREBRO_AGENT_NAME
            memory_client.py             # MemoryClient
            docs_client.py                # DocsClient
        tests/
    cerebro-mcp/                  # single stdio MCP server (FastMCP): memory_* + docs_*
        pyproject.toml              # [project.scripts]: cerebro-mcp
        src/cerebro_mcp/server.py
        tests/
    cerebro-cli/                   # single CLI: cerebro memory / cerebro docs / backup|restore|cross-cutting token
        pyproject.toml               # [project.scripts]: cerebro
        src/cerebro_cli/
            main.py                    # build_parser(), loads .env.production/.env before dispatching
            dotenv.py                    # minimal .env parser, without overriding the environment already present
            tokens.py                     # generation/local persistence of pending cross-cutting secrets
            memory_commands.py             # cerebro memory ...
            docs_commands.py                # cerebro docs ...
            shared_commands.py               # backup, restore, cross-cutting token create/revoke
        tests/
```
