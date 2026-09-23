# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `CLAUDE.md`, `LICENSE` (MIT) and this `CHANGELOG.md`.

## [2.5.0] - 2026-09-23

Unified skill (previously memory-only) and internal gateway, so a deployment can
expose `/memory`, `/docs`, `/flows` behind a single port.

### Added
- Multi-file `cerebro` skill (`SKILL.md` + `references/{memory,docs,flows}.md`),
  replaces the previous memory-only skill — covers both execution and authoring
  of flows.
- `gateway` service (Caddy) in `compose.yaml`, `full` profile: routes by prefix
  (`handle_path`) to each API, host port configurable
  (`CEREBRO_GATEWAY_HOST_PORT`).

### Changed
- `DEPLOY.md`/`README.md`: the recommended external reverse proxy setup is now a
  single `reverse_proxy` to the gateway, instead of one block per service (the
  previous pattern is kept as a documented alternative).

## [2.4.0] - 2026-09-23

`cerebro-flows`: new module, a step-by-step workflow execution engine ("traffic
light") — a flow is defined in YAML and revealed to the model one step at a
time, never in full, so a blocking checkpoint is a real server-side limit
instead of an instruction the model could skip.

### Added
- `packages/cerebro-flows` package: YAML schema with referential validation
  (`schema.py`), execution engine (`engine.py`) on top of Redis (the mutable
  pointer of a run) + Postgres (versioned definitions and append-only
  `flow_run_events`), FastAPI, `001_init.sql` migration.
- `FlowsClient` in `cerebro-clients`.
- 13 `flow_*` MCP tools in `cerebro-mcp`, including `flow_validate` for
  iterative authoring without saving.
- Minimal definition CRUD in `cerebro-cli` (`cerebro flow ...`), no execution
  commands (a flow is driven by a model, not by a person typing at a CLI).
- `redis` service in `compose.yaml` (AOF persistence), `cerebro-flows-api`
  service.

### Fixed
- `from __future__ import annotations` in `cerebro_flows/api.py` broke
  FastAPI's `Depends()` resolution for nested dependencies — found while
  running integration tests against real Postgres.
- YAML "Norway problem": decision branches named `yes`/`no` were resolved as
  booleans by PyYAML's default resolver — custom loader that only interprets
  `true`/`false` as real booleans.
- `start_run` didn't mark a run as `completed` when the `entry` step itself was
  terminal with no prerequisites — centralized in `_enter_step`.

## [2.3.0] - 2026-09-23

`cerebro-docs` work block (distinct from its initial creation in `2.0.0`): real
archiving, hidden/locked categories, slug redirects, version history, and bulk
import — the 6 items that had been left open.

### Added
- Real archiving (`documents.status`): `docs_archive`/`docs_unarchive`/
  `docs_list_archived`, distinct from `docs_delete` — same pattern as
  `memory_forget` in cerebro-memory.
- `hidden` (toggleable) and `locked` (permanent, set-once-at-creation)
  categories — meant for supporting content that should never be listed (e.g.
  `cerebro-flows` reference documents).
- Bulk markdown importer (`cerebro docs import-markdown`), without distilling
  content (unlike the memory one).
- Slug redirects (`slug_redirects` table): `docs_get` prepends an explicit
  `alert` when it resolves through a redirect, so the model corrects the old
  path instead of just reporting it.
- `document_versions` reader (`docs_history`), read-only, no automatic restore.
- `002_archive_hidden_redirects.sql` migration.

### Fixed
- Defensive normalization in `docs_patch_section`: a heading passed with its
  `#`/`##` prefix included is now treated the same as without it.

## [2.2.0] - 2026-09-23

### Added
- Integration test isolation: each package drops/recreates an ephemeral
  `cerebro_test` database in a `pytest_configure` hook (not a fixture — `api.py`
  caches `get_settings()` during the *collection* phase, before any fixture
  gets to run), instead of running against the development database.

### Changed
- Host ports for all services made configurable via `.env`
  (`POSTGRES_HOST_PORT`, `CEREBRO_MEMORY_HOST_PORT`, `CEREBRO_DOCS_HOST_PORT`),
  needed to run alongside another shared Postgres on the remote test server.

## [2.1.0] - 2026-08-17

### Changed
- `cerebro-memoria` skill updated to also cover `docs_*` (previously
  memory-only), removes references to "KnowledgeOS".

## [2.0.0] - 2026-08-12

`cerebro` ecosystem: `knowledgeos` stops being a single tool and becomes a
multi-module monorepo. See `docs/ecosistema-cerebro.md` for the full definition
and the reasoning behind each decision.

### Added
- **`cerebro-docs`** (new module): repository of complete markdown documents
  (not distilled, unlike memory), categories as a formal table (FK, not
  repeated text), versioning (`document_versions`), partial per-section patches
  (`docs_patch_section`, transactional with `SELECT ... FOR UPDATE`).
- **`cerebro-clients`**: shared thin httpx SDK (`MemoryClient`/`DocsClient`),
  with no business logic of its own.
- **`cerebro-mcp`**: single MCP server (previously `knowledgeos-mcp` with
  memory only), exposes `memory_*` and `docs_*` from one stdio process.
- **`cerebro-cli`**: single CLI (`cerebro <module> <subcommand>`), includes
  cross-service `token create`/`revoke` (one secret, registered separately in
  each API, with an explicit partial-failure report if one of the two fails).
- Security and quality audit as a mandatory gate (SS15): `q` in searches
  parameterized against SQL injection, `allowed_categories` verification.

### Changed
- **Rename `knowledgeos` → `cerebro-memory`**, moved to
  `packages/cerebro-memory`, migrated to the `cerebro_memory` schema (previously
  `public`) without losing existing data.
- Single `compose.yaml`: one Postgres, one schema per module (`cerebro_memory` +
  `cerebro_docs`), never the same container for two services.
- `knowledgeos backup`/`restore` become `cerebro backup`/`restore`, still a
  simple `pg_dump`/`psql` — covers both schemas with no code change since they
  share one instance.

### Breaking
- Anything pointing at the `knowledgeos` package/CLI/MCP server stops existing
  under that name — requires reconfiguring MCP clients and reinstalling the
  CLI.

## [1.0.1] - 2026-08-08

### Fixed
- Postgres password via `.env` and host port 8005 in the VPS deployment
  (previously hardcoded).

### Added
- VPS deployment guide (`DEPLOY.md`).

## [1.0.0] - 2026-08-07

First release, under the name `knowledgeos` (before the rebrand to the `cerebro`
ecosystem and the monorepo restructuring in `2.0.0`).

### Added
- **Phase 0**: decision to build instead of adopt (documentary and empirical
  evaluation of mem0/Zep-Graphiti/Letta — none of them treats isolating
  contexts that share vocabulary as a first-class problem, see
  `decisions/000-build-vs-adopt.md`); v2 plan and initial evaluation suite.
- **Phase 1 (core)**: Postgres + pgvector, FastAPI, hybrid retrieval (RRF),
  memory supersedence, audit log, local embeddings (fastembed); MCP server with
  6 tools.
- **Phase 2**: Context Engine — automatic per-context scoping, ambiguity
  protocol (`ambiguous`/`candidates`) and `context_preferences` learning.
- **Phase 3**: lightweight memory graph — typed edges (`memory_link`),
  `memory_related` (1-hop expansion), `memory_timeline`.
- **Phases 4 and 5**: CLI, disambiguation dataset for a future local classifier,
  resolver hook (`AmbiguityResolver`/`OllamaResolver`, off by default), markdown
  importer.
- Scoped tokens (`read`/`write`/`admin`) and `allowed_contexts`, full Docker
  setup, `allowed_contexts` leak fixes found in the final audit.

[Unreleased]: https://github.com/luisjdev0/cerebro/compare/main...docs/claude-md-license-changelog
[2.5.0]: https://github.com/luisjdev0/cerebro/commit/286e526
[2.4.0]: https://github.com/luisjdev0/cerebro/commit/75d6758
[2.3.0]: https://github.com/luisjdev0/cerebro/commit/0d17897
[2.2.0]: https://github.com/luisjdev0/cerebro/commit/0b1935c
[2.1.0]: https://github.com/luisjdev0/cerebro/commit/d7825d5
[2.0.0]: https://github.com/luisjdev0/cerebro/commit/c5c06c5
[1.0.1]: https://github.com/luisjdev0/cerebro/commit/a4e49f5
[1.0.0]: https://github.com/luisjdev0/cerebro/releases/tag/v1.0.0
