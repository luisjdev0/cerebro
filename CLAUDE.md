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
| `cerebro-auth` | Owns the shared `cerebro_auth` schema (users, groups, tokens); memory/docs/flows validate every request against it directly | 8000 |
| `cerebro-clients` | Shared thin httpx SDK (`MemoryClient`/`DocsClient`/`FlowsClient`/`AuthClient`) — no business logic | — |
| `cerebro-mcp` | Single MCP server (`FastMCP`, stdio) exposing `memory_*`/`docs_*`/`flow_*`/`auth_*` as tools | — |
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
   (`Authorization: Bearer`). memory/docs/flows validate every request by reading
   the shared `cerebro_auth` schema directly (cross-schema query, same Postgres,
   no network hop) — see "Auth model" below. `cerebro-auth` itself is the one
   service that owns token/user/group management (`POST /tokens`, `/users`,
   `/groups`, `/login`).
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

## Auth model (`cerebro_auth` schema)

A single shared schema (`packages/cerebro-auth`) backs authentication for all
three content services. Two gates apply per request, independent of each other:

1. **What's visible** — `allowed_modules` (which of memory/docs/flows a token can
   touch at all) + `module_scopes` (fine-grained contexts/categories within a
   module). `access_level` (`user`/`owner`/`admin`, on `users` if the token has one,
   else a fallback column on the token itself) controls how this resolves: `admin`
   ignores `module_scopes` entirely; `owner` inherits the union of their groups'
   `group_scopes`, narrowed (never widened) by anything the token adds on top;
   `user` uses the token's own values as-is.
2. **Who owns it** — `owner_user_id` on `memories`/`documents`/`flow_definitions`
   (who created it). `user` sees only their own; `owner` sees everyone who shares a
   group with them (via `user_groups`); `admin`/root sees everything. This is
   separate from gate 1 and stacks on top of it.

The root token (`.env`'s `API_TOKEN`) still bypasses both gates with no DB row, same
as before unification. Token/user/group management (`cerebro token create`,
`cerebro user create`, `cerebro group ...`, MCP `auth_*` tools) lives exclusively in
`cerebro-auth` — memory/docs/flows only read the schema, they don't write to it.
`cerebro login --token <token>` persists credentials to `~/.cerebro/config.json`,
which `cerebro-clients` reads as a fallback below environment variables.

**asyncpg does not auto-decode `jsonb` columns** — always `json.loads()` a
`module_scopes`/similar column read if it comes back as `str` (no codec is
registered anywhere in this monorepo); a real bug from skipping this shipped in two
services before being caught in live testing.

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

- **Auth unification + user system — DONE**, merged to `main`. See "Auth model"
  above. Not yet implemented: password-based login (`users.password_hash` exists,
  nullable, no endpoint/flow yet — token-only login for now).
- **Remote backups — DONE**. `POST /backup` on `cerebro-auth` (admin-only) streams
  a full `pg_dump` of the whole shared Postgres instance straight to the client
  (`StreamingResponse`, no buffering, no JSON envelope — same as a browser
  download). `cerebro backup` downloads from it instead of shelling out to
  `docker compose exec`, so it works against any deployment the caller has an
  admin token for. Extraction only — no restore, no scheduling, no MCP tool
  (same rationale as `docs import-markdown`: dumping raw SQL into a model's
  context has no sane use case). The runtime image installs
  `postgresql-client-17` via the PGDG apt repo (codename read from
  `/etc/os-release`, not hardcoded — the base image's own Debian codename can
  change across rebuilds).
- **Standalone CLI installer — DONE**. First GitHub Actions workflow in the repo
  (`.github/workflows/release-cli.yml`, tag-triggered, Linux+Windows matrix)
  builds generic `cerebro` binaries with PyInstaller and publishes them to GitHub
  Releases. `gateway/install/install.sh`/`install.ps1` (served statically by the
  gateway at `/install/*`) download the right one for `CEREBRO_REPO` (default
  `luisjdev0/cerebro`, so a fork points at its own Releases) and place it on the
  PATH. Deliberately minimal: the script only installs the binary — it does not
  know this deployment's URL or any token, and does not touch
  `~/.cerebro/config.json` itself. Configuring the CLI is a separate, manual step
  with the already-implemented `cerebro login --token <token> --url <gateway>`.
