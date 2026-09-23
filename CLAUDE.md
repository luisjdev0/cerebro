# CLAUDE.md

Guide for Claude Code (or any agent) working in this repository.

## What this repo is

Monorepo for the **cerebro** ecosystem (`luisjdev0/cerebro`): a set of self-hosted
services that give a model persistent memory, complete documents, and workflows
("traffic light" pattern) — meant to be used via MCP or CLI. It is not a
single-user product: anyone can clone the repo, deploy their own instance, and
(if they fork) modify it.

Modules (each an independent Python package under `packages/`):

| Package | What it is | Internal port |
|---|---|---|
| `cerebro-memory` | Semantic/episodic/procedural/decision memory, with a Context Engine (automatic scoping) | 8000 |
| `cerebro-docs` | Complete markdown documents, categories, versioning, redirects | 8000 |
| `cerebro-flows` | Step-by-step workflow execution engine ("traffic light"), Redis + Postgres | 8000 |
| `cerebro-clients` | Shared thin httpx SDK (`MemoryClient`/`DocsClient`/`FlowsClient`) — no business logic | — |
| `cerebro-mcp` | Single MCP server (`FastMCP`, stdio) exposing `memory_*`/`docs_*`/`flow_*` as tools | — |
| `cerebro-cli` | CLI (`cerebro <module> <subcommand>`) on top of `cerebro-clients` | — |

Each internal API runs on port 8000 inside its container; `compose.yaml` maps
each one to a different host port via `.env` (`CEREBRO_MEMORY_HOST_PORT`, etc. —
never hardcode ports, everything is `${VAR:-default}`).

## Architecture: 4-layer pattern

Every new feature in a module follows the same path, in this order:

1. **SQL migration** (`packages/<package>/db/migrations/`) — each service has its
   own Postgres schema (`cerebro_memory`, `cerebro_docs`, `cerebro_flows`), all in
   the **same** shared Postgres instance (`pgvector/pgvector:pg17`).
2. **FastAPI API** (`packages/<package>/src/<package>/api.py`) — token auth
   (`Authorization: Bearer`), `read`/`write`/`admin` scopes, plus fine-grained
   restrictions per category/context (`allowed_categories`/`allowed_contexts`).
   Auth is currently duplicated per service — **being unified**, see "Work in
   progress" below.
3. **Client in `cerebro-clients`** — one method per HTTP endpoint, zero business
   logic, just payload translation.
4. **MCP tools in `cerebro-mcp/server.py`** (and mirrored commands in
   `cerebro-cli`) — each tool is a thin adapter: calls the client, translates the
   result or exception to `{"error": "..."}`. No business logic here either —
   that lives in the API.

`from __future__ import annotations` **must not be added** to any `api.py` that
defines nested dependencies (`Depends(get_pool)` inside `create_app()`) — it
breaks FastAPI's dependency resolution (see the fix commit in `cerebro-flows`, a
real bug already hit once).

## Testing

Each package isolates its integration tests against an ephemeral `cerebro_test`
database, via a `pytest_configure` hook in `tests/conftest.py` (**not a
fixture** — `api.py` instantiates `app = create_app()` at module level, which
caches `get_settings()` during pytest's *collection* phase, before any fixture
gets to run). The hook drops/recreates `cerebro_test` and overrides
`DATABASE_URL`/`REDIS_URL` before any test module is imported. Without a
reachable Postgres/Redis, suites skip cleanly (they don't fail).

```bash
cd packages/<package>
pip install -e ".[dev]"
pytest
```

## Expected workflow (important, not just a suggestion)

- **All new development goes on a branch**, never straight to `main`.
- **Integration tests run on the remote test server**
  (`192.168.0.101`, SSH alias `root@192.168.0.101` or via user `luisjdev`), not
  on the local machine or through a tunnel — there's a persistent checkout of the
  repo there with Postgres and Redis already running. That server is **shared**
  with other projects (WordPress, RabbitMQ, an unrelated Postgres on 5432/`pgdb`)
  — check occupied ports (`ss -tln`) before starting anything new there, never
  assume a default port is free.
- **Never merge a new or large module to `main` without explicit approval** from
  the user in chat, even if all tests pass. For small, clearly low-risk changes,
  use judgment, but when in doubt, ask.
- The actual deployment (production VPS, `cerebro.luisjdev.com`) is a separate
  step, after the merge, and also requires being explicitly requested — see
  `DEPLOY.md`.

## Internal gateway (`gateway/`)

`gateway` service in `compose.yaml` (Caddy, `full` profile): routes `/memory`,
`/docs`, `/flows` by prefix (`handle_path`) to each API, and exposes a single
port (`CEREBRO_GATEWAY_HOST_PORT`). Meant so an external Caddy (a VPS or anyone
else's) only needs one `reverse_proxy` instead of one block per service. Any new
module added to the ecosystem must add its own `handle_path` in
`gateway/Caddyfile`, instead of forcing whoever deploys it to touch their own
reverse proxy.

## Conventions

- **Config**: `pydantic-settings`, one `config.py` per package, sensible
  defaults + environment variable overrides. Never hardcode URLs/ports/tokens.
- **Errors in MCP tools**: always `{"error": "<message>"}`, never let a raw
  exception propagate to the model.
- **"Earn your complexity"**: don't build infrastructure (local classifier, eval
  harness, new services) before there's real evidence it's needed — several
  design decisions in this project cite this criterion explicitly.
- **Hidden** (`hidden`) and **locked** (`locked`, `cerebro-docs` only today)
  categories/contexts: used for supporting content that shouldn't show up in
  normal listings (e.g. reference prompts for flows). Don't invent new hidden
  categories without a real reason.

## Versioning

SemVer (`MAJOR.MINOR.PATCH`) at the repo level, with git tags. Notable changes go
in `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com) format) under
`[Unreleased]` until the next release.

## License

MIT (see `LICENSE`). As long as the project has a single author (Jose Luis Ortiz
Sánchez), his copyright lets him relicense future versions if he decides to —
what's already published under MIT stays MIT for anyone who already has that
copy. If external contributions (third-party PRs) are ever accepted, that
flexibility is lost for the code they contribute, unless there's a contributor
license agreement in place.

## Work in progress / recent decisions not implemented yet

See `luisjdev-pendientes/ecosistema-cerebro` in `cerebro-docs` for the detailed,
up-to-date state of everything pending. Summary of the most relevant items as of
this writing:

- **Auth unification + user system**: today each service
  (`memory`/`docs`/`flows`) has its own duplicated `api_tokens` table. Agreed
  design (not implemented): a single shared `cerebro_auth` schema for the three
  (tokens with `allowed_modules` + `module_scopes`), later extended with
  `users`/`user_tokens`/`groups`/`user_groups`/`group_scopes` for the user system
  (`user`/`owner`/`admin` levels). Management operations (login, creating a
  user/token/group, backups) will live in a new `cerebro-auth` service; per-request
  *validation* stays local to each service, reading the shared schema directly
  (no network hop per request).
- **Standalone CLI installer**: agreed design (not implemented) — CI (GitHub
  Actions, Linux+Windows matrix) compiles generic binaries and publishes them to
  GitHub Releases; an installer script (`.sh`/`.ps1`) served by the gateway itself
  at `/install/*` downloads straight from Releases when run (no polling, no
  binary volume on the server) and configures that instance's URL as local
  config. The repo to download from is a variable (`CEREBRO_REPO`), not
  hardcoded, so a fork points at its own Releases.
- **Remote backups via API**: blocked until `cerebro_auth` exists (needs to know
  which schemas a given token/user can see).
