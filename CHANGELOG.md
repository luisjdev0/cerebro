# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Portal Documents screen: table with category and status filters, sortable columns, full-text search, a persisted rows-per-page selector and a pagination bar that stays in place; a resizable side panel that renders the document's Markdown (GFM tables, code, links) with its version history, inline editing with a Write/Preview switch (including moving it to another category), archive/restore and delete; a "New document" panel; and a categories panel to create, rename, hide and delete categories. New `cerebro-docs` endpoint `GET /documents/browse` (paginated, sortable, `status` active|archived|all, full-text `q`, returns an excerpt and a `total`, same category/ownership gates as `GET /documents`); it is consumed by the portal only, so there is deliberately no client/MCP/CLI mirror.
- Portal document viewer: Preview / raw Markdown switch and a copy-to-clipboard button (also in the editor; works over plain http via an `execCommand` fallback); in-document `#anchor` links and footnote links now scroll to their heading inside the panel instead of opening a new tab (headings get GitHub-style ids).
- Portal "New document" panel: drag-and-drop (or click) upload of `.md`/`.txt` files. One file fills the form so it can be reviewed first; several files open an import list (title from the first `# heading`, identifier from the file name, same rule as `cerebro docs import-markdown`) with a choice to skip or update documents whose identifier already exists, and a per-file result summary. Done client-side with the existing `POST/PATCH /documents` calls, since there is no bulk endpoint.
- Portal Memory screen: paginated list with context/type/status filters and sortable columns, semantic search, and a detail panel with metadata and related memories (deep-linkable with `?id=`). New `cerebro-memory` endpoints `GET /memories` (paginated browse: `context`, `type`, `status`, `sort`, `order`, `limit`, `offset`; returns `items` and `total`) and `GET /memories/{id}`, both honoring the context scope and the ownership gate. They are consumed by the portal only, so there is deliberately no client/MCP/CLI mirror (MCP already has search and timeline).
- Portal Memory screen, second pass: roomier filter card with labeled controls and a clear-filters button, every column sortable (the `GET /memories` `sort` now also accepts `title`, `type` and `context`), a persisted rows-per-page selector with a range indicator, "New memory" and "New context" dialogs (permission errors from the API are shown as messages), and a redesigned detail panel with inline content editing (stored as a new version), Forget with confirmation, and a width the user resizes by dragging its left edge (remembered, keyboard accessible).
- `cerebro-memory`: `PATCH /memories/{id}` now accepts `title`, `type`, `importance`, `confidence` and `occurred_at` besides `content` (all optional, at least one required). A new `content` still stores a new version; metadata-only edits are made in place so an importance tweak does not fill the history with superseded rows. New `PATCH /contexts/{slug}` edits `name`, `kind` and `description` (the slug is immutable because token and group scopes reference it). Mirrored in `MemoryClient.update_memory`/`update_context`, MCP (`memory_update` extended; new `memory_update_context` and `memory_delete_context`, 46 tools in total) and the CLI (`cerebro memory update`, `cerebro memory context list|create|update|delete`).
- Portal: slim scrollbars in the theme colors (Ixchel hides every scrollbar by default; the sidebar keeps its own rule and stays hidden).
- Portal Memory screen: the detail panel edits title, type, importance and date as well as the content (edit mode shows only the form, with nothing repeated), and a "Manage contexts" panel creates, renames, re-describes and deletes contexts (deleting is admin-only on the API; the confirmation states how many memories go with it). "New memory" and context management open in the same resizable side panel as the detail view instead of modals, sharing one remembered width. Paging keeps the pagination bar in place (the table holds its height and previous rows stay visible while the next page loads), so it can be clicked repeatedly.
- Portal overview: the activity heatmap now fills the card width, labels weekdays and has a GitHub-style year selector (last 12 months plus every year with data); the three dashboard blocks (cards, activity, charts) can be reordered by drag and drop (pointer or keyboard) and the order is saved per user in the browser, with a "restore default layout" link. `GET /activity` of memory, docs and flows accepts `year` (calendar year, overrides `days`) and returns `years` (years with visible activity).
- Portal credits line (creator, MIT license, GitHub) on every screen: prominent in the login panel, discreet inside the portal.
- Portal login: split layout with a side panel (tagline, copy-ready CLI install and login commands built from the URL the portal is served on, and the creator/MIT license credit).
- Portal overview: three charts (memories, documents, flows) under the activity heatmap, each switchable between a line (created/updated per month over the last year) and a pie (memories per context, documents per category, flows per category; the 5 biggest slices plus "Other"). `GET /stats` of `cerebro-docs` and `cerebro-flows` gained `documents_by_category` / `flows_by_category` (honoring category scope and the ownership gate); existing fields are unchanged.
- Portal i18n (English/Spanish) with `i18next`: language switcher in the top-right corner of every screen, including the login; the choice is persisted in `localStorage` (`cerebro.lang`) and defaults to the browser language. The login is a custom form because Ixchel's `LoginScreen` ships fixed Spanish copy.
- `cerebro-portal`: a React + Vite + TypeScript + Tailwind web portal (Ixchel
  components) served by its own nginx container behind the gateway, which now routes
  everything that is not an API prefix to it. First screens: username/password login
  (same session tokens as the CLI) and an overview with clickable module cards and a
  GitHub-style activity heatmap. Client-side routes are `/memories`, `/documents`,
  `/workflows` and `/admin` so they never collide with the API prefixes.
- `GET /activity?days=N` in `cerebro-memory`, `cerebro-docs` and `cerebro-flows`:
  per-day counts (created / updated; for flows, definitions created / runs started),
  honoring the token's scope and the ownership gate. Feeds the heatmap.
- `scripts/seed_demo.py`: fills a **disposable** instance with simulated users,
  contexts, memories, documents and flows, backdated over ~6 months.
- `CEREBRO_GATEWAY_BIND` in `compose.yaml` (default `127.0.0.1`) to expose the gateway
  on the LAN of a throwaway test server.

## [2.8.2] - English everywhere

### Changed
- Portal login always lands on the overview (it no longer returns to the screen the session was left on).
- Everything user- or model-facing that was still Spanish is now English: API error
  messages of `cerebro-docs`, `cerebro-flows` (including the step-by-step engine and
  the YAML schema validation errors) and `cerebro-memory` (secret-rejection message,
  Context Engine disambiguation prompt, importer truncation marker), every runtime
  message of the MCP server (errors, "not found" / "already exists" / "no access"
  notices, the moved-document `alert`, ambiguity message and learning note), the
  `skills/cerebro` skill (`SKILL.md` and its four references) and the evaluation suite
  docs and harness output (`research/alternativas.md` is now `research/alternatives.md`).
- Spanish remains only as data: the evaluation datasets (`evals/memories.yaml`,
  `evals/cases.yaml`), Spanish text-handling test fixtures and the Context
  Engine's accent-folding table.

### Added
- Repo-wide guard test (`cerebro-cli/tests/test_repo_english.py`) that fails when
  accented characters or Spanish prose appear in tracked files outside the allowed data.

## [2.8.1] - Installer and client messages in English

### Changed
- `install.sh` / `install.ps1` print English messages, and their next-step hint is now
  just `cerebro login` (interactive; username/password or token) instead of a
  token-only command.
- `CerebroConnectionError` message of `cerebro-clients` is English.

## [2.8.0] - Phase 1B: English-only CLI

### Changed
- Every user-facing string of the CLI (`--help`, menus, prompts, messages and errors)
  is now in English, matching the rest of the repository. The CLI is English-only by
  decision: no i18n layer and no language selection.
- Tests assert the English messages, and a new guard test
  (`test_english_only.py`) fails if accented characters or obvious Spanish words
  reappear in user-facing strings.

### Changed
- The CLI release is now automatic: merging to `main` a change whose newest
  CHANGELOG heading (`## [X.Y.Z]`) has no release yet builds the binaries and
  publishes release `vX.Y.Z` (the tag is created by the workflow). Manual `v*`
  tags and `workflow_dispatch` still work.

## [2.7.0] - Phase 1A: interactive CLI and user/password login

### Added
- **Interactive CLI**: `cerebro` alone opens a menu; `cerebro <module>` (or any
  group of subcommands) opens its submenu; missing required arguments are asked.
  Built on `rich` + `questionary`, walking the existing argparse tree (no second
  command definition). Never prompts without a TTY; `--no-interactive` /
  `CEREBRO_NO_INTERACTIVE` forbid it. Without a session only login is offered.
- **User/password login**: `cerebro login --user` (prompt, `--password-stdin`,
  `CEREBRO_PASSWORD`, or the discouraged inline `--password=`), `cerebro logout`,
  `cerebro user create --password`, `cerebro user set-password`.
- **cerebro-auth**: argon2id passwords, `POST /login/password`, `POST /logout`,
  `POST /users/{name}/password`, `has_password` on users, expiring session tokens
  (`api_tokens.expires_at`), per-user lockout (migration `002`). New settings
  `SESSION_TTL_HOURS`, `MAX_FAILED_ATTEMPTS`, `LOCKOUT_MINUTES`.
- `cerebro-clients`: `AuthClient.login_password`, `logout`, `set_password`.
- GitHub Actions workflow `tests.yml` running every package's suite against
  Postgres 17 + pgvector and Redis.

### Changed
- memory/docs/flows treat an expired token like a revoked one.
- `~/.cerebro/config.json` now also stores `kind`, `user` and `expires_at`.
- Docs: 44 MCP tools (was 36 in README/DEPLOY/manual), `cerebro-auth` documented as
  implemented.

### Security
- Passwords never reach the MCP layer (no tool by design); generic `401` for all
  bad-credential cases with timing equalisation; password changes close sessions.

## [2.6.1] - 2026-09-27

### Fixed
- The standalone CLI binary crashed on startup (`IndexError`) before parsing any
  arguments: `REPO_ROOT` assumed a source checkout (`Path(__file__).parents[4]`),
  which doesn't hold once PyInstaller freezes the file into a temp extraction
  directory. Found by actually running the packaged `v2.6.0` binary, not just
  building it — that release's binaries never worked and were pulled
  (`gh release delete v2.6.0`, tag removed). `cerebro backup`'s default output
  directory also no longer depends on `REPO_ROOT` (it never touched the repo
  tree to begin with) — now `~/cerebro-backups`.

## [2.6.0] - 2026-09-27

Unified authentication (`cerebro-auth`), plus remote backups and a standalone
CLI installer built on top of it.

### Added
- **`cerebro-auth`** (new module): shared `cerebro_auth` schema (`users`,
  `groups`, `user_groups`, `group_scopes`, `api_tokens`) backing every
  service's identity. Two independent gates per request — which
  modules/contexts are visible (`allowed_modules`/`module_scopes`,
  `access_level`) and who owns the content (`owner_user_id` on
  `memories`/`documents`/`flow_definitions`). `cerebro login`, `cerebro
  user`/`group` commands, 8 `auth_*` MCP tools.
- `POST /backup` on `cerebro-auth` (admin-only): streams a full `pg_dump` of
  the whole shared Postgres instance straight to the client, no buffering, no
  JSON envelope. `cerebro backup` now downloads from it instead of shelling
  out to `docker compose exec`, so it works against any deployment with an
  admin token.
- First GitHub Actions workflow (`release-cli.yml`): builds generic `cerebro`
  CLI binaries (Linux/Windows, PyInstaller) on tagged pushes and publishes them
  to GitHub Releases. `gateway/install/install.sh`/`install.ps1`, served
  statically by the gateway at `/install/*`, download the right binary
  (configurable via `CEREBRO_REPO` for forks) and leave token/URL
  configuration to `cerebro login`.

### Changed
- `cerebro-memory`/`cerebro-docs`/`cerebro-flows` lost their own local
  `api_tokens` tables and `/tokens` routes — every request now validates
  against the shared `cerebro_auth` schema (cross-schema query, same
  Postgres, no network hop).

### Breaking
- Any token issued before this release (against a service's own local
  `api_tokens` table) stops authenticating — a new token must be issued
  through `cerebro-auth` (`POST /tokens` or `cerebro token create`).

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

[Unreleased]: https://github.com/luisjdev0/cerebro/compare/v2.6.1...main
[2.6.1]: https://github.com/luisjdev0/cerebro/commit/90c4dd1
[2.6.0]: https://github.com/luisjdev0/cerebro/commit/a433869
[2.5.0]: https://github.com/luisjdev0/cerebro/commit/286e526
[2.4.0]: https://github.com/luisjdev0/cerebro/commit/75d6758
[2.3.0]: https://github.com/luisjdev0/cerebro/commit/0d17897
[2.2.0]: https://github.com/luisjdev0/cerebro/commit/0b1935c
[2.1.0]: https://github.com/luisjdev0/cerebro/commit/d7825d5
[2.0.0]: https://github.com/luisjdev0/cerebro/commit/c5c06c5
[1.0.1]: https://github.com/luisjdev0/cerebro/commit/a4e49f5
[1.0.0]: https://github.com/luisjdev0/cerebro/releases/tag/v1.0.0
