# cerebro — Technical Manual

This is a reference manual for developers who want to understand cerebro's
architecture deeply enough to contribute, extend, or self-host it. It documents
internals: data models, API surfaces, protocols, and the design decisions behind
them, verified against the source in this repository as of the state of `main`.

It intentionally does **not** cover day-to-day usage (that's a separate User
Manual) or "how an AI agent should behave in this repo" (that's `CLAUDE.md`,
which this manual assumes you've read for the contribution workflow: branching
rules, the test-server policy, merge approval, etc. — those facts are not
repeated here).

This manual supersedes the original monorepo-migration decision log
(`ecosistema-cerebro.md`, written in Spanish and superseded in places by later
work such as `cerebro-flows` and the gateway) as the canonical architecture
reference. That original document is archived outside this repo, not deleted
— ask if you need to consult it for historical context.

---

## 1. Architecture overview

### 1.1 What cerebro is

cerebro is a self-hosted ecosystem that gives an AI model (or several,
model-agnostically) three complementary long-term capabilities:

- **Persistent memory** — short, atomic, distilled facts/events/decisions with
  hybrid retrieval and automatic scoping (`cerebro-memory`).
- **Complete documents** — full Markdown documents that are deliberately *not*
  distilled, with categories, versioning, and archiving (`cerebro-docs`).
- **Step-by-step workflow execution** — a "traffic light" engine that reveals a
  YAML-defined procedure to a model one step at a time, with decisions and
  human-approval checkpoints (`cerebro-flows`).

All three are consumed the same way: via a single MCP server (`cerebro-mcp`) or
a single CLI (`cerebro-cli`), both built on a shared, business-logic-free HTTP
SDK (`cerebro-clients`).

### 1.2 Monorepo rationale

The repo is a single monorepo (`packages/`) rather than one repo per service.
The reasoning: the actual shared
resource is *deployment* — one `compose.yaml`, one Postgres instance — and a
polyrepo would need an "umbrella" repo with `include:` plus sibling checkouts
on disk just to reconstruct that, complexity that exists only to preserve repo
separation, not to solve a real problem. A monorepo keeps `compose.yaml` at the
root with no tricks, while every package still builds and deploys
independently (each service package has its own `Dockerfile`). The usual
monorepo cost — mixed versioning across modules moving at different speeds —
doesn't apply here: there are no third parties consuming these packages and no
CI depending on separate tags. If a module ever needs to be split out, that's a
mechanical `git filter-repo`/`git subtree split` operation, paid only if it's
ever actually needed ("earn your complexity").

### 1.3 The four-layer pattern

Every feature, in every service module, is implemented through the same four
layers, always in this order:

1. **SQL migration** (`packages/<package>/db/migrations/*.sql`) — plain
   numbered `.sql` files, applied in filename order by a tiny runner in
   `db.py`, tracked in a `schema_migrations` table. Each service owns one
   Postgres **schema** (`cerebro_memory`, `cerebro_docs`, `cerebro_flows`), all
   in the **same shared Postgres instance** (`pgvector/pgvector:pg17`).
2. **FastAPI API** (`packages/<package>/src/<package>/api.py`) — token auth
   (`Authorization: Bearer`), `read`/`write`/`admin` scopes, plus fine-grained
   per-token restriction (`allowed_contexts` in memory, `allowed_categories` in
   docs/flows). All business logic lives here (or in modules it imports, e.g.
   `retrieval.py`, `context_engine.py`, `sections.py`, `engine.py`) — nothing
   below this layer has its own logic.
3. **Client in `cerebro-clients`** (`memory_client.py` / `docs_client.py` /
   `flows_client.py`) — one method per HTTP endpoint, a 1:1 mapping, zero
   validation or business logic of its own — only payload translation and
   error normalization (`CerebroAPIError` / `CerebroConnectionError`).
4. **MCP tools** (`cerebro-mcp/src/cerebro_mcp/server.py`) and **CLI
   subcommands** (`cerebro-cli/src/cerebro_cli/*_commands.py`) — thin
   adapters: each tool/command calls a client method and translates the
   result or exception into either a normal return value or, for MCP tools,
   `{"error": "<message>"}` — an MCP tool must never let a raw exception
   propagate to the calling model.

A documented real bug from following this pattern: `from __future__ import
annotations` must **not** be added to any `api.py` that defines nested
dependencies (`Depends(get_pool)` declared *inside* `create_app()`) — it breaks
FastAPI's dependency resolution at runtime, because FastAPI inspects live
function objects/annotations to build the dependency graph, and the
`__future__` import turns every annotation into a lazily-evaluated string.
`cerebro-flows` hit this for real; the fix commit is in its history.

### 1.4 Shared Postgres, one schema per service, Redis only for flows

One Postgres instance (`pgvector/pgvector:pg17`, container `cerebro-postgres`)
hosts three logical schemas:

| Schema | Owner service | Extensions it relies on |
|---|---|---|
| `cerebro_memory` | `cerebro-memory` | `vector` (pgvector, HNSW index), `pgcrypto` |
| `cerebro_docs` | `cerebro-docs` | `pgcrypto` |
| `cerebro_flows` | `cerebro-flows` | `pgcrypto` |

`vector`/`pgcrypto` are installed in `public` (shared extensions, not owned by
any one service) and each service's connection pool sets
`search_path = "<its schema>, public"` so unqualified SQL in its migrations
lands in the right schema while extension types/functions still resolve via
the `public` fallback. On a *fresh* database, a service's early migrations run
before its own `CREATE SCHEMA` exists yet (or, for `cerebro-memory`, before
`005_schema_cerebro_memory.sql` runs), so Postgres falls back to creating
things in `public` — that migration then moves everything into the schema in
the same startup run, making it idempotent whether the database is fresh or
pre-existing.

Cross-schema access between services is a hard rule: **never**. If a memory
needs to reference a full document, it does so via a URI in its content
(`cerebro-docs://category/slug`), never an FK/join — the same convention
`security.py` already uses for secret references (`secret://env/name`).

**Redis is used by `cerebro-flows` only.** It stores exclusively the mutable
*pointer* of an in-progress run (`flow_run:<run_id>` → current step id, pending
checkpoint state), never the flow's full definition — see §5.4. `compose.yaml`
documents that Redis isn't exclusive to flows architecturally (any future
module needing fast ephemeral state could use the same instance/database
index), it's just flows' first consumer.

### 1.5 Deployment topology

Three ways to run the stack, all supported by the same `compose.yaml`:

- **Local dev**: only Postgres (+Redis) in Docker (`docker compose up -d`, no
  profile), each API run directly with Python
  (`python -m cerebro_memory.main`, etc.) for instant reload.
- **Full Docker** (`docker compose --profile full up -d`): Postgres, Redis, and
  all three APIs as containers, each built from its own multi-stage
  `Dockerfile`. `cerebro-memory`'s image pre-downloads the embeddings model at
  *build* time so the container starts in seconds, not minutes.
  `docker compose up -d` without `--profile full` still only starts
  Postgres+Redis — day-to-day local dev is unaffected by the profile's
  existence.
- **Gateway-fronted**: the `full` profile also brings up `cerebro-gateway`
  (Caddy), which exposes all three APIs behind one port via path-prefix
  routing — see §6.

Each internal API listens on port 8000 *inside* its container; `compose.yaml`
maps each to a different host port via `.env`
(`CEREBRO_MEMORY_HOST_PORT`/`CEREBRO_DOCS_HOST_PORT`/`CEREBRO_FLOWS_HOST_PORT`,
defaults 8005/8006/8007) — nothing is ever hardcoded, everything is
`${VAR:-default}`.

```
Agent (Claude / GPT / Gemini / custom)
        |
        v
   cerebro-mcp (stdio)  or  cerebro-cli
        |                                  thin adapters, no business logic
        v                                  of their own (except import-markdown,
  cerebro-clients (shared httpx SDK)        backup/restore)
        |
        +----------------+----------------+----------------+
        v                v                v
  cerebro-memory API   cerebro-docs API   cerebro-flows API      (FastAPI)
        |                |                |
        v                v                v
  cerebro_memory       cerebro_docs      cerebro_flows      (Postgres schemas,
  schema                schema            schema + Redis     same instance)
```

---

## 2. Per-package deep dive

### 2.1 `cerebro-memory`

**Responsibility**: semantic/episodic/procedural/decision memory with hybrid
(vector + full-text) retrieval, a no-LLM "Context Engine" for automatic scope
resolution, a lightweight relationship graph, and a timeline view. This is the
oldest package (it began life as the standalone project `knowledgeos`; several
identifiers — the `kos_` token prefix, the default Postgres user/db name
`knowledgeos`, the Docker volume `knowledgeos-pgdata` — still carry that name
on purpose, because renaming them would orphan real production data rather
than migrate it).

**Data model** (`db/migrations/001_init.sql` → `005_schema_cerebro_memory.sql`):

| Table | Purpose | Notable columns |
|---|---|---|
| `contexts` | The unit of isolation — a project, client, or life domain. First-class, not a free-text tag. | `slug` (unique), `name`, `kind` (`project`\|`domain`\|`person`\|`org`), `description` (used by the LLM to disambiguate) |
| `memories` | The atomic unit of memory. | `context_id` FK, `type` (`semantic`\|`episodic`\|`procedural`\|`decision`), `importance`/`confidence` (0..1), `status` (`active`\|`superseded`\|`archived`), `superseded_by` (self-FK), `occurred_at`, `embedding vector({{EMBEDDING_DIM}})` |
| `audit_log` | Append-only record of who did what. | `agent`, `action` (`search`\|`remember`\|`update`\|`forget`\|…), `memory_id`, `detail JSONB` |
| `disambiguation_log` | Every Context Engine scoping decision, resolved or pending. | `query`, `candidates JSONB`, `chosen_context`, `resolved_by` (`auto`\|`agent`\|`user`\|`local_model`, NULL until resolved) |
| `context_preferences` | Learned `(context, term) → weight` boosts, fed by resolved disambiguations. | `context_id` FK, `term`, `weight`, unique `(context_id, term)` |
| `memory_edges` | Explicit directed relationships between memories — a lightweight graph **in Postgres**, not a dedicated graph database. | `from_memory`/`to_memory` FKs (`ON DELETE CASCADE`), `relation` (CHECK-constrained vocabulary), unique `(from, to, relation)` triple, `CHECK (from_memory <> to_memory)` |
| `api_tokens` | Named, scoped bearer tokens (see §4). | `token_hash` (SHA-256, never plaintext), `scopes TEXT[]`, `allowed_contexts TEXT[]` (NULL = all) |

`{{EMBEDDING_DIM}}` in `001_init.sql` is a template placeholder substituted by
`db.py` with `settings.embedding_dimension` before execution, so the pgvector
column width always matches the configured embedding model.

`memories_embedding_hnsw_idx` (HNSW/cosine) and `memories_fts_idx` (GIN,
`to_tsvector('spanish', title || ' ' || content)`) are the two indexes hybrid
retrieval fuses over.

**Notable design decisions**:

- **Hybrid retrieval via Reciprocal Rank Fusion** (`retrieval.py`): a vector
  (HNSW cosine) ranking and a full-text (Spanish `tsvector`) ranking are each
  computed independently (top `candidate_pool=50` each), then combined with
  standard RRF (`score(id) = Σ 1/(k+rank)`, `k=60`, Cormack et al.) rather than
  a hand-tuned weighted blend. `reciprocal_rank_fusion`/`fuse_rankings` are
  pure functions with no I/O, kept separate specifically so they're
  unit-testable without a database.
- **The Context Engine does not call an LLM** (`context_engine.py`). The
  "auxiliary model" for scope decisions is deliberately the model already in
  the conversation — Phase 2's whole premise is that automatic scoping should
  be cheap and deterministic: an unfiltered preliminary retrieval (top ~20),
  scored per context as `RRF-sum + context_preferences boost + explicit-mention
  boost`, decided by two independently-tunable thresholds (dominance *and*
  margin over the runner-up, so a 51/49 split is never declared "dominant").
  If nothing dominates, 2–4 candidate contexts are returned *with* a few real
  results each as evidence, rather than silently merging memories from
  different contexts. See §"API reference" and `config.py` for the exact
  threshold variables and their calibrated defaults.
- **An optional local classifier is wired in but off by default**
  (`AmbiguityResolver` / `NullResolver` / `OllamaResolver`, Phase 4). It is
  invoked *only* after the deterministic Phase 2 scoring has already decided a
  case is ambiguous — it never replaces that scoring, only tries to resolve
  what it already gave up on. Any failure (Ollama unreachable, timeout,
  unparseable response, a slug outside the candidate set) falls silently back
  to the ambiguous-to-agent behavior; the docstring is explicit that "this must
  NEVER be able to break a search." Gated on purpose behind `CONTEXT_ENGINE_RESOLVER=ollama`
  because there is, by design, no training data to justify it yet (~500 real
  disambiguations is the documented threshold before it's worth revisiting;
  `GET /disambiguations/export` / `cerebro memory export-disambiguations`
  exists to produce that dataset when the time comes).
- **Updates never edit in place.** `PATCH /memories/{id}` inserts a *new* row
  and marks the old one `status='superseded', superseded_by=<new id>` inside a
  transaction with `SELECT ... FOR UPDATE` — the full history survives, and
  retrieval simply excludes `superseded` rows unless `include_superseded=true`
  is passed.
- **The relationship graph lives in the same Postgres, not a dedicated graph
  database** (`graph.py`, `memory_edges` table) — an explicit project rule.
  The relation vocabulary is a Postgres `CHECK`, not free text:
  `relates_to`, `caused_by`, `part_of`, `contradicts`, `follows`. A second,
  *virtual* relation, `supersedes`, is derived on read from
  `memories.superseded_by` and is never materialized as a row in
  `memory_edges` — `get_related()` synthesizes it. `GET
  /memories/search?expand=true` reuses the same neighbor lookup to attach a
  separate `related` block (never merged into `results`, so it can't perturb
  retrieval metrics) for the top 3 hits, with a cross-context rule: a neighbor
  from a *different* context than the one the search resolved to is only
  included if reached via an *explicit* edge (an intentional bridge), never via
  the virtual supersedence chain.
- **Credential-leak rejection** (`security.py`): `POST /memories` and `PATCH
  /memories/{id}` run the content (and title) through a small set of regexes
  (AWS access keys, GitHub tokens, `sk-...`-style API keys, Slack tokens,
  inline `password=`/`secret=` assignments, connection strings with embedded
  credentials) and reject (422) anything that matches, pointing the caller at
  the sanctioned `secret://<env>/<name>` reference convention instead. This is
  `cerebro-memory`-only; `cerebro-docs` deliberately does *not* filter content
  (§2.2).
- **Embedding provider is an abstraction, not a hardcoded model call**
  (`embeddings.py`): `EmbeddingProvider` is an ABC with separate
  `embed_query`/`embed_passage` methods (kept distinct because some model
  families, e.g. E5, need different prefixes for queries vs. stored passages —
  the current default model doesn't, so both delegate to the same call today).
  `build_embedding_provider()` tries `FastEmbedProvider` (local, CPU-only, ONNX
  via `fastembed`, no API keys) first and falls back to
  `SentenceTransformersProvider` only if `fastembed` fails to import.
- **The Markdown importer distills, on purpose** (`markdown_importer.py`):
  cerebro-memory's whole model is "memory over conversation" — short, distilled
  facts, not source dumps. Its importer recognizes three formats in preference
  order (Claude Code frontmatter → `MEMORY.md` index → generic heading-split
  Markdown) and unconditionally truncates any fenced code block longer than 30
  lines to `[código truncado]` before anything else — a full source dump is
  exactly what this package is *not* meant to store (that's `cerebro-docs`'
  job). This module is pure parsing (filesystem only, no API/DB calls) so it's
  testable with fixtures alone; orchestration (dedup search, `POST /memories`
  calls, 422 handling, the final report) lives in `cerebro-cli`.

### 2.2 `cerebro-docs`

**Responsibility**: a repository of complete Markdown documents — the opposite
end of the spectrum from memory. Explicitly **not** formal memory: no
retrieval semantics, no distillation, no truncation. Categories, full version
history, partial patches by section, archiving, hidden/locked categories, and
slug redirects.

**Data model** (`db/migrations/001_init.sql`, `002_archive_hidden_redirects.sql`):

| Table | Purpose | Notable columns |
|---|---|---|
| `categories` | Formal, explicit-creation grouping — like `contexts` in memory, not a copied string. | `slug` (unique), `name`, `description`, `hidden BOOLEAN`, `locked BOOLEAN` (CHECK: `locked` requires `hidden`) |
| `documents` | Full Markdown, never truncated. | `category_id` FK, `slug`, `title`, `content`, `status` (`active`\|`archived`), `search_vector TSVECTOR` (generated/STORED column, always in sync, no trigger needed), unique `(category_id, slug)` |
| `document_versions` | Full snapshot taken *before* every write. | `document_id` FK (`ON DELETE CASCADE`), `content`, `title`, `category_id`, `version_number`, unique `(document_id, version_number)` |
| `slug_redirects` | "Symlink"-style record of an old `(category, slug)` coordinate pointing at the current `document_id`. Never chained. | `old_category`, `old_slug`, `document_id` FK (`ON DELETE CASCADE`), unique `(old_category, old_slug)` |
| `api_tokens` | Same shape as memory's, with `allowed_categories` instead of `allowed_contexts`. | see §4 |

**Notable design decisions**:

- **`category_id` is a foreign key, never a copied string** — for the same
  reason `contexts` is a table in memory: renaming a category is *one* row
  updated (`PATCH /categories/{slug}`), and every document's logical route
  (`/{category}/{slug}`) changes for free when the join resolves, without
  touching `documents` at all.
- **Slug collisions fail loudly, never auto-suffix or silently overwrite.**
  `UNIQUE (category_id, slug)` at the DB level; at the API level, a collision
  on `POST /documents` returns 409 pointing at the existing document and
  suggesting `PATCH /documents/{id}` — the same conservative "never guess"
  criterion applied throughout the codebase (see `Edit`-tool-style ambiguity
  handling below).
- **Mandatory write transactionality**: every content-changing endpoint
  (`PATCH /documents/{id}`, `PATCH /documents/{id}/section`) does `SELECT ...
  FOR UPDATE OF d`, then inserts the *pre-write* snapshot into
  `document_versions`, then applies the `UPDATE` — all inside one transaction.
  Without the row lock taken *before* the read, two near-simultaneous requests
  could both read the same "previous" content and produce a duplicated
  snapshot instead of a true version chain; Postgres's own serialization of
  concurrent `UPDATE`s on the same row is what makes "no extra locking beyond
  this" sufficient (no optimistic-locking mechanism was added on top).
- **Partial patch by heading** (`sections.py`, `PATCH
  /documents/{id}/section`): a *section* is defined as "from a heading to the
  next heading of the same level or higher" — deliberately re-implemented here
  (not shared as a package) from the same idea as
  `cerebro_memory.markdown_importer.HEADING_RE`, generalized to heading levels
  1–6 instead of 1–2 because full documents can be arbitrarily deep, unlike
  distilled top-level memories. Operations: `replace`, `append`,
  `insert_after`, `insert_before`, `delete`. A duplicate (ambiguous) heading
  **always** fails, even with `create_if_missing=true` — it is never guessed,
  the same criterion Claude Code's own `Edit` tool applies to `old_string`
  uniqueness. `create_if_missing=true` appends a new section at the given
  `new_heading_level` (default 2) when the heading doesn't exist yet.
- **Hidden and locked categories**: `hidden=true` removes a category from `GET
  /categories`/unfiltered `GET /documents` listings, but it's still directly
  reachable by anyone who knows its exact slug (creating/reading a document in
  it isn't filtered, only the *listing* is) — toggleable via `PATCH
  /categories/{slug}`. `locked=true` can only be set at creation time (there is
  no endpoint to unset it, ever) and requires `hidden=true` — meant for
  reference content that must never become navigable (e.g. the support `.md`
  files a `cerebro-flows` step might reference internally).
- **Archiving is a status flag, not title-prefix hackery.** An earlier
  approach literally prefixed titles with `[ARCHIVADO]`; migration 002
  replaces that with a real `status` column plus a dedicated listing endpoint
  (`GET /documents/archived`), never mixed into the normal listing.
- **Slug redirects, never chained.** Renaming a document or moving/renaming its
  category writes a redirect row pointing the *old* `(category, slug)`
  coordinate straight at the current `document_id` (never at another old
  slug). `GET /documents/{category}/{slug}` only consults `slug_redirects`
  when the direct lookup 404s — a real document at that exact route always
  wins. The `docs_get` MCP tool surfaces `redirected_from` back to the model so
  it can stop referencing the stale route.
- **No content filtering — an explicit, documented decision, not an
  oversight.** Unlike `POST /memories`, `cerebro-docs` accepts any content: a
  runbook or infra note sometimes legitimately needs to show a connection
  string shape or a placeholder secret. The security implication (a backup
  dump may therefore contain real secrets pasted in by mistake) is handled at
  the backup-file-permissions level (`cerebro backup` writes `0600`,
  best-effort on Windows), not by filtering document content.
- **Strict input validation.** All write-request models inherit `StrictIn`
  (`ConfigDict(extra="forbid")`) — an unknown field in a request body is a 422,
  never silently ignored. Without this, a client typo (e.g. sending `content`
  instead of `body` in a section patch) would silently fall through to a
  field's default and could wipe content — the same "never guess, always
  error" philosophy as slug collisions and ambiguous headings.

### 2.3 `cerebro-flows`

**Responsibility**: a "traffic light" execution engine for step-by-step
procedures defined in YAML. It is a sequencing/information service, not an
orchestrator — the model still performs the real actions (via its own tools);
`cerebro-flows` only controls what the model is allowed to see next and blocks
progress past unapproved checkpoints.

**Data model** (`db/migrations/001_init.sql`):

| Table | Purpose | Notable columns |
|---|---|---|
| `flow_categories` | Namespace *and* the human-readable id prefix for its flows (`code`, e.g. `INC` → `INC-22`). Independent of `cerebro-docs`' `categories` — no FK/join across schemas. | `slug`, `code` (both unique), `name`, `description` |
| `flow_definitions` | A flow's identity. The YAML itself never lives here — only a pointer to its current version. | `category_id` FK, `code` (human-readable id, unique), `status` (`active`\|`archived`), `current_version INT` |
| `flow_definition_versions` | Full YAML snapshot per version — same pattern as `document_versions`. A run stays anchored to the version it started with. | `definition_id` FK, `version_number`, `yaml_content`, unique `(definition_id, version_number)` |
| `flow_runs` | The *immutable* header of one execution — which definition/version it started with, how it ended. The mutable pointer (current step) lives in Redis, not here. | `run_id`, `definition_id` FK, `definition_version INT`, `status` (`in_progress`\|`completed`\|`aborted`) |
| `flow_run_events` | Append-only audit trail, one row per real transition, written **at the moment it happens** (not only at the end) — survives even if the run is abandoned and its Redis TTL expires. | `run_id` FK (`ON DELETE CASCADE`), `event_type`, `step_id`, `payload JSONB` |
| `api_tokens` | Exact mirror of `cerebro-docs`', `allowed_categories` over flow categories. | see §4 |

**Notable design decisions** (see also §5 for the full protocol):

- **The model never sees the whole procedure.** `flow_start`/`flow_next`
  reveal exactly one step (or, for a `parallel_group`, its sibling steps
  together) per call. This is the entire point of the "traffic light" name:
  "the model shouldn't skip a checkpoint" becomes "the model cannot see the
  next step until it clears the current one," a structural guarantee instead
  of a behavioral request.
- **Redis holds only the mutable pointer; Postgres is re-read on every step.**
  `flow_run:<run_id>` in Redis is `{flow_code, definition_id,
  definition_version, current_step_id, status, pending_checkpoint,
  last_activity_at}` with a *sliding* TTL (`FLOW_RUN_TTL_HOURS`, default 72,
  renewed on every `flow_next`/checkpoint call). The full parsed definition is
  **never cached** — `engine.py` reloads and re-validates the YAML from
  `flow_definition_versions` on every single call. This keeps Redis a pure
  ephemeral cache: if a run's Redis key expires from inactivity, its identity
  and audit trail (`flow_runs`/`flow_run_events`) still exist in Postgres —
  `get_run_state`/`_require_pointer` distinguish "expired, but the run is
  still `in_progress` in Postgres" (a specific error message suggesting
  `flow_start` again) from "genuinely doesn't exist" from "already
  `completed`/`aborted`".
- **A synthetic first step for prerequisites.** If a flow's YAML declares a
  `tools:` list, `flow_start` doesn't jump straight to `entry` — it first
  returns a synthetic step with `id: "__prerequisites__"` and
  `tools_required`. The *check itself* is the model's job (try `ToolSearch`,
  flag it to the user if something is genuinely missing) — the server only
  forces this step to appear first in the protocol; it does no tool
  introspection of its own.
- **The "Norway problem" is explicitly disabled in the YAML parser**
  (`schema.py`, `_FlowYamlLoader`): standard YAML 1.1 resolves bareword `yes`
  /`no`/`on`/`off` to booleans, which would silently corrupt a `decision`
  step's `branches` keyed `"yes"`/`"no"` (the single most natural naming for a
  yes/no decision). The loader strips those implicit boolean resolvers while
  keeping `true`/`false` intact (used deliberately for `terminal`/
  `checkpoint.required`).
- **Step shape is validated per type**, via a pydantic `model_validator`:
  `type: decision` *requires* `branches` (and forbids `next`); any other type
  forbids `branches`; `type: delegate` requires the `delegate` block (any
  other type forbids it); `terminal: true` forbids both `next` and `branches`.
- **Referential integrity is checked at save/validate time, not at run time.**
  `FlowDefinitionSchema._referential_integrity` checks: no duplicate step ids,
  `entry` exists, every `next`/`branches.*`/`checkpoint.on_reject` target
  exists, every `parallel_group`'s member steps share the exact same `next`
  target (so a parallel fan-out has one unambiguous rejoin point), every
  `rules[].applies_to` references a real step id, and at least one step has
  `terminal: true`. All failures raise `InvalidFlowSchemaError` naming the
  specific step/field — this is what `POST /flows/validate` (and the
  `flow_validate` MCP tool) exists for: iterating on authoring errors one at a
  time instead of guessing.
- **A checkpoint blocks `flow_next` until explicitly resolved.** If the
  *current* step has `checkpoint.required: true` and it hasn't been approved,
  `advance()` raises `CheckpointPendingError` (409) rather than silently
  proceeding. `flow_approve_checkpoint`/`flow_reject_checkpoint` are separate,
  deliberate tool calls — never a parameter `flow_next` could be called with
  "out of habit" — the same criterion applied to `memory_forget`/
  `docs_archive` elsewhere in the ecosystem: the highest-consequence action in
  a flow should never be reachable by accident. Rejecting a checkpoint doesn't
  stop the run — it jumps to `checkpoint.on_reject`'s target step (e.g. "redo
  this step" or "go back to review"), logged as `checkpoint_rejected`.
- **`abort` is irreversible and distinct from a rejected checkpoint.** A
  rejected checkpoint reroutes the flow; `flow_abort` ends it (`status:
  'aborted'`) and can only be called on an `in_progress` run.

### 2.4 `cerebro-clients`

**Responsibility**: the *only* code shared between `cerebro-mcp` and
`cerebro-cli` beyond simple imports — three thin, synchronous `httpx.Client`
wrappers (`MemoryClient`, `DocsClient`, `FlowsClient`), one method per HTTP
endpoint, doing payload translation and nothing else. This is a deliberate,
narrow exception to "duplicate rather than couple": `cerebro-cli` and
`cerebro-mcp` are two *transports* over the exact same calls, unlike
memory/docs/flows, which are different domains and duplicate their `auth.py`
on purpose (see §4).

- **`base.py`** (`BaseClient`): wraps `httpx.Client` with the `Authorization:
  Bearer` and `X-Agent-Name` headers baked in at construction, and translates
  errors into exactly two types — `CerebroAPIError` (status ≥ 400; carries
  `status_code` and `detail`, parsed from the server's `{"detail": ...}` JSON
  body when possible) and `CerebroConnectionError` (network-level failure:
  DNS, connection refused, timeout). It never swallows an error silently, and
  it never does its own retries or "friendly" translation — that belongs to
  the presentation layers above it. A `transport` constructor parameter exists
  purely as a test seam (`httpx.MockTransport`), not production configuration.
- **`config.py`**: resolves base URLs/tokens/agent name from environment
  variables with an explicit precedence order per client. For
  `MemoryClient` only, a legacy fallback to `KNOWLEDGEOS_API_URL`/
  `KNOWLEDGEOS_API_TOKEN`/`KNOWLEDGEOS_AGENT_NAME` is preserved — those are
  variables a user's environment may already have set from before this SDK
  layer existed, and the migration explicitly asked not to break them.
  `cerebro-docs`/`cerebro-flows` are new services with no legacy variables to
  preserve, so they only read `CEREBRO_DOCS_URL`/`CEREBRO_FLOWS_URL`. All
  three share a single `CEREBRO_TOKEN` (the transversal-token model, §4) and
  `CEREBRO_AGENT_NAME`.

| Variable | Applies to | Fallback |
|---|---|---|
| `CEREBRO_MEMORY_URL` | memory | `KNOWLEDGEOS_API_URL` → `http://localhost:8005` |
| `CEREBRO_DOCS_URL` | docs | → `http://localhost:8010` |
| `CEREBRO_FLOWS_URL` | flows | → `http://localhost:8020` |
| `CEREBRO_TOKEN` | all three | `KNOWLEDGEOS_API_TOKEN` (memory only) → empty (no auth) |
| `CEREBRO_AGENT_NAME` | all three | `KNOWLEDGEOS_AGENT_NAME` (memory only) → `cerebro-client` |

### 2.5 `cerebro-mcp`

**Responsibility**: a single stdio MCP server (`mcp`'s `FastMCP`) exposing all
three services as 36 tools, prefixed `memory_*` (10) / `docs_*` (13) /
`flow_*` (13) so the families never get confused. It is unified deliberately
because the MCP layer never had business logic of its own — it's a thin
adapter over HTTP either way, and talking to three APIs instead of one doesn't
couple the *backends* to each other (they still share no schema, no runtime
dependency); the coupling is contained in the presentation layer, which is
where it's cheap to pay. The accepted cost is a shared failure blast radius (a
bug in `docs_*` tools could, in principle, take down a stdio process also
serving `memory_*`/`flow_*` mid-conversation) — acceptable for a personal/small
ecosystem, and the kind of thing that would motivate splitting the MCP server
again if the ecosystem became multi-tenant/high-availability.

The full tool list:

| Family | Tools |
|---|---|
| `memory_*` | `memory_search`, `memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`, `memory_create_context`, `memory_stats`, `memory_link`, `memory_related`, `memory_timeline` |
| `docs_*` | `docs_create_category`, `docs_categories`, `docs_save`, `docs_get`, `docs_search`, `docs_list`, `docs_update`, `docs_patch_section`, `docs_delete`, `docs_archive`, `docs_unarchive`, `docs_list_archived`, `docs_history` |
| `flow_*` | `flow_create_category`, `flow_categories`, `flow_validate`, `flow_save`, `flow_get`, `flow_list`, `flow_update`, `flow_delete`, `flow_start`, `flow_next`, `flow_approve_checkpoint`, `flow_reject_checkpoint`, `flow_abort` |

Notable behavior beyond a pure pass-through:

- **Every tool converts exceptions to `{"error": "..."}`.** Three helper
  functions (`_connection_error_message`, `_auth_error_message`,
  `_http_error_message`) format `CerebroConnectionError`/`CerebroAPIError`
  consistently per-service, so a model always gets an actionable message
  ("cerebro-docs API returned 404: ...") instead of a stack trace.
- **`memory_search` auto-resolves ambiguity across calls.** The server keeps a
  single process-memory slot, `_last_disambiguation_id` — deliberately *not* a
  stack/history, just enough to bridge "an ambiguous search" → "the next
  search from the same agent with an explicit `context`". If `memory_search`
  comes back `mode="ambiguous"`, its `message` is pre-formatted with the
  candidate contexts and evidence for the model to decide or show the user; if
  the *next* `memory_search` call passes an explicit `context`, the server
  infers that's how the ambiguity was resolved and automatically calls `POST
  /disambiguations/{id}/resolve` — no separate tool call needed from the model
  — which in turn grows `context_preferences` so similar queries resolve
  faster next time.
- **`docs_get` surfaces stale-route warnings.** If a document is only found via
  `slug_redirects` (not a direct hit), the tool's response carries an alert
  telling the model to stop using the old route and correct it wherever it was
  saved.

Startup: `python -m cerebro_mcp.server`, or the console entry point
`cerebro-mcp` (`cerebro_mcp.server:main`, `mcp.run(transport="stdio")`).
Requires all three APIs reachable at the URLs above.

### 2.6 `cerebro-cli`

**Responsibility**: a single CLI (`cerebro`) mirroring the MCP server's
structure — per-module subcommands (`cerebro memory ...`, `cerebro docs ...`,
`cerebro flow ...`) plus ecosystem-level commands with no module prefix
(`cerebro backup`, `cerebro restore`, `cerebro token create/revoke`). Built
entirely on `cerebro-clients`, so it takes exactly the same HTTP path as the
MCP server — no parallel implementation of any API call.

- **`main.py`** wires an `argparse` tree: `memory` → `stats`/
  `export-disambiguations`/`import-markdown`/`token {create,list,revoke}`;
  `docs` → `category {create,list,rename,delete}`/`save`/`get`/`list`/
  `search`/`update`/`patch-section`/`delete`/`archive`/`unarchive`/`history`/
  `import-markdown`/`stats`; `flow` → `category {create,list}`/`validate`/
  `save`/`get`/`list`/`update`/`delete`/`stats` (note: **running** a flow is
  not a CLI concern — only a model does that, via `flow_*` MCP tools; the CLI
  only manages flow *definitions*). Shared, module-less subcommands: `backup`,
  `restore`, `token create`/`token revoke`.
- **Cross-cutting ("transversal") tokens** (`tokens.py`,
  `shared_commands.cmd_backup`/token commands): `cerebro token create <name>
  --scopes ...` generates **one** secret (prefix `cbr_`, distinct from each
  service's own self-generated prefix — `kos_` for memory, `cbrd_` for docs,
  `cbrf_` for flows) and registers it independently in every service that
  accepts named tokens, via each service's `POST /tokens` with `value=<the
  same secret>`. **Partial-failure safety**: before the first registration
  attempt, the generated secret is persisted to a local file outside the repo
  (`~/.cerebro/pending-tokens/<name>.json`, directory `0700`/file `0600`,
  best-effort on Windows where `os.chmod` doesn't model the same bitmask) and
  only deleted once *every* service confirms success. If one service accepts
  the registration and another fails, re-running the exact same command reuses
  the *same* secret from that pending file — registration is idempotent by
  name+hash on the server side (`create_api_token`'s `UniqueViolationError`
  handling returns the existing row if the hash matches instead of raising),
  so retrying never produces two different secrets under one name, and never
  silently leaves the state half-done. `cerebro token revoke <name>` revokes
  in every service; a 404 in a service where it was already gone counts as
  success. `main()` warns on startup (via `warn_stale_pending_tokens`) about
  any pending file older than 24h, so a forgotten partial registration doesn't
  sit on disk indefinitely without anyone noticing — it never prints the
  plaintext secret in that warning, only the name and age.
- **`backup`/`restore`** (`shared_commands.py`): `cerebro backup` shells out to
  `docker compose exec -T postgres pg_dump -U knowledgeos knowledgeos`,
  writing to a directory **outside the repo tree** by default
  (`../cerebro-backups/`, sibling of the repo) with `chmod 0600` on the output
  file — because a single shared Postgres instance means one dump already
  covers every service's schema in one operation, and because `cerebro-docs`
  content isn't filtered for secrets (§2.2), the dump file itself is treated
  as sensitive. `cerebro restore <file>` requires typed `yes` confirmation
  unless `--yes` is passed, then pipes the file into `psql` the same way.
- **`import-markdown`** reuses `cerebro_memory.markdown_importer` directly
  (pure parsing, no API calls in that module) and does the orchestration
  itself: search for near-duplicates, call `POST /memories`, handle 422s
  (credential rejections) gracefully, print a final report. This is why
  `cerebro-cli` depends on the `cerebro-memory` *package* (for its parser),
  not just `cerebro-clients` — see its `pyproject.toml`.

---

## 3. API reference

Method/path/scope tables below are transcribed directly from each service's
`api.py` route decorators — not from a design doc — so they reflect exactly
what's implemented. "Scope" is the `require_scope(...)` dependency guarding
the route; `admin` does **not** imply `read`/`write`.

### 3.1 `cerebro-memory`

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/health` | none | DB connectivity check, no auth |
| POST | `/tokens` | admin | create a named token (`name`, `scopes`, `allowed_contexts?`, `value?`) |
| GET | `/tokens` | admin | list tokens (no hashes/plaintext) |
| DELETE | `/tokens/{name}` | admin | revoke a token by name |
| POST | `/contexts` | write | create a context (`slug`, `name`, `kind`, `description?`) |
| GET | `/contexts` | read | list contexts (filtered to `allowed_contexts`) |
| DELETE | `/contexts/{slug}` | admin | delete a context; 409 if it has memories unless `?force=true` (cascades hard-delete of its memories + their edges) |
| POST | `/memories` | write | create a memory; rejects credential-looking content (422) |
| GET | `/memories/search` | read | hybrid retrieval: `q`, `context?`, `scope?` (`auto`\|`all`\|`<slug>`, default `auto`), `type?`, `limit?` (1–50, default 5), `include_superseded?`, `expand?` |
| PATCH | `/memories/{id}` | write | creates a new version and supersedes the old one (never edits in place) |
| DELETE | `/memories/{id}` | write | `?hard=false` (default) archives; `?hard=true` hard-deletes (cascades edges) |
| POST | `/memories/{id}/edges` | write | create an edge `{to_memory, relation, note?}`; 422 bad relation/self-link, 404 missing endpoint, 409 duplicate triple |
| DELETE | `/memories/{id}/edges/{edge_id}` | write | delete an edge (must touch `{id}`) |
| GET | `/memories/{id}/related` | read | 1-hop neighbors (both directions) + virtual supersedence chain; `relation?` filter |
| GET | `/timeline` | read | `episodic`/`decision` memories by effective date; `context?`, `from?`, `to?`, `limit?` (default 50) |
| POST | `/disambiguations/{id}/resolve` | write | resolve a pending Context Engine disambiguation, grows `context_preferences` |
| GET | `/disambiguations/export` | admin | raw disambiguation dataset (`resolved_only?`) for local-classifier training data |
| GET | `/stats` | read | memories by context/status, disambiguation counts, learned preferences |

### 3.2 `cerebro-docs`

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/health` | none | DB connectivity check, no auth |
| GET | `/stats` | read | counts of categories/documents/versions |
| POST | `/tokens` | admin | create a named token (`allowed_categories?` instead of contexts) |
| GET | `/tokens` | admin | list tokens |
| DELETE | `/tokens/{name}` | admin | revoke a token |
| POST | `/categories` | write | create a category (`slug`, `name`, `description?`, `hidden?`, `locked?`) |
| GET | `/categories` | read | list non-`hidden` categories (filtered to `allowed_categories`) |
| PATCH | `/categories/{slug}` | write | rename/edit (incl. `hidden`); registers slug redirects for its documents if the slug changes; 409 if `locked` and trying `hidden=false` |
| DELETE | `/categories/{slug}` | admin | 409 if it has documents unless `?force=true` (cascades documents + their versions) |
| POST | `/documents` | write | create a document (`title`, `content`, `category`, `slug?`); 409 on slug collision in that category |
| GET | `/documents/{id}/versions` | read | full version history, most recent first, read-only (registered before the `{category}/{slug}` route to avoid ambiguity) |
| GET | `/documents/{category}/{slug}` | read | read by exact route; falls back to `slug_redirects` if no direct match |
| GET | `/documents` | read | list `status=active`, `updated_at desc`; `category?`, `q?` (full-text), `limit?` (1–100, default 20), `offset?` |
| GET | `/documents/archived` | read | same shape, `status=archived`, dedicated listing |
| PATCH | `/documents/{id}` | write | full replace (can move category); snapshots the old version first |
| PATCH | `/documents/{id}/section` | write | partial patch by heading (`operation`: replace/append/insert_after/insert_before/delete) |
| POST | `/documents/{id}/archive` | write | soft-delete (reversible) |
| POST | `/documents/{id}/unarchive` | write | reverse an archive |
| DELETE | `/documents/{id}` | write | hard delete (cascades version history) — irreversible |

### 3.3 `cerebro-flows`

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/health` | none | DB **and** Redis connectivity check, no auth |
| GET | `/stats` | read | counts of categories/flows/runs |
| POST | `/tokens` | admin | create a named token (`allowed_categories?` over flow categories) |
| GET | `/tokens` | admin | list tokens |
| DELETE | `/tokens/{name}` | admin | revoke a token |
| POST | `/categories` | write | create a flow category (`slug`, `code`, `name`, `description?`) |
| GET | `/categories` | read | list categories |
| POST | `/flows/validate` | write | validate YAML without saving it — returns the specific error |
| POST | `/flows` | write | create a definition (`category`, `yaml_content`, `code?`, auto-generated if omitted) |
| GET | `/flows/{code}` | read | full current-version YAML |
| GET | `/flows` | read | list definitions (`category?`, `limit?`, `offset?`) |
| PATCH | `/flows/{code}` | write | replace the YAML — creates a new version; in-progress runs keep the version they started with |
| DELETE | `/flows/{code}` | write | delete (cascades versions + runs) |
| POST | `/flows/{code}/start` | write | start a run → `{run_id, status, step}` |
| POST | `/runs/{run_id}/next` | write | advance to the next step; body `{decision?}` if the current step is a decision |
| POST | `/runs/{run_id}/approve-checkpoint` | write | approve the current step's checkpoint |
| POST | `/runs/{run_id}/reject-checkpoint` | write | reject it — jumps to `checkpoint.on_reject` |
| POST | `/runs/{run_id}/abort` | write | abort an in-progress run — irreversible |
| GET | `/runs/{run_id}` | read | current state of a run |

---

## 4. Auth model

### 4.1 Current model (implemented today)

Every endpoint except `/health` requires `Authorization: Bearer <token>`, in
all three services. There are two kinds of accepted credential, resolved by
each service's `get_principal()` FastAPI dependency into a `Principal`
dataclass (`name`, `scopes: frozenset`, `allowed_contexts`/`allowed_categories:
frozenset | None`, `is_root: bool`):

1. **The root token** — `Settings.api_token` from that service's `.env`
   (`API_TOKEN`). Compared with `secrets.compare_digest` (constant-time). Has
   all three scopes over every context/category, unconditionally, and has no
   row in `api_tokens` at all. Each service has its **own** root token — they
   are not the same value by default. Intended for bootstrap/yourself, not for
   handing to individual agents.
2. **Named tokens** — minted via each service's `POST /tokens` (or the CLI).
   Stored as `SHA-256(token)` in that service's own `api_tokens` table; the
   plaintext is shown exactly once, at creation, and can never be recovered
   (only revoked and re-created). Each has explicit `scopes` and an optional
   allow-list (`allowed_contexts`/`allowed_categories`, `NULL` = unrestricted).

Scope vocabulary (`VALID_SCOPES = ("read", "write", "admin")`), identical
across all three services:

| Scope | cerebro-memory | cerebro-docs | cerebro-flows |
|---|---|---|---|
| `read` | every `GET` except `/health` | same | same |
| `write` | create/update/delete of memories, edges, contexts (create), disambiguation-resolve | create/update/delete of documents, categories (create/edit) | create/update/delete of flow definitions, categories (create), everything in the execution engine |
| `admin` | `/tokens/*`, `GET /disambiguations/export`, `DELETE /contexts/{slug}` | `/tokens/*`, `DELETE /categories/{slug}` | `/tokens/*` |

**`allowed_contexts`/`allowed_categories` enforcement**, applied consistently
per service (three places in memory; write/read-by-exact-route + listing
narrowing in docs/flows):

- An **explicit** out-of-scope target (e.g. `GET /memories/search?context=x`
  where `x` isn't allowed) is a hard `403`.
- **Listings with no explicit target** are silently narrowed to the allowed
  set — never a 403, the token just doesn't see what it can't access. In
  `cerebro-memory`'s `scope=auto` (Context Engine) this goes further: a
  disallowed context is dropped *before scoring even happens* — it can never
  win as auto-scope, never appear as an "ambiguous" candidate, and its
  name/description never leak to a restricted token.
- **Writes/reads-by-exact-id/route** on a resource whose context/category is
  outside the allow-list are `403`. A subtlety in `cerebro-memory` (documented
  in `api.py`): a lookup that resolves a context slug from a memory/edge id
  before checking `allowed_contexts` treats "not found" (slug is `None`) as
  *not* an authorization failure — the underlying 404 is left to surface on
  its own, so a restricted token can't use the 403-vs-404 distinction to probe
  whether something exists outside its allow-list.

**Token identity overrides the caller-supplied header.** The optional
`X-Agent-Name` header names the calling agent for `memory.source` /
`documents.created_by` / `audit_log.agent` (default `"unknown"`) — but a
*named* token's `name` always wins over it (it's the real, non-self-declared
identity backing that credential). Only the root token — which has no fixed
identity of its own — actually falls back to `X-Agent-Name`.

**Token prefixes** (cosmetic but consistent, useful for recognizing a leaked
value's origin): `kos_` (cerebro-memory, legacy from `knowledgeos`), `cbrd_`
(cerebro-docs), `cbrf_` (cerebro-flows), `cbr_` (transversal tokens minted by
`cerebro token create`, §2.6).

**Auth is duplicated across all three services today, by design, not
oversight.** `cerebro_memory/auth.py`, `cerebro_docs/auth.py`, and
`cerebro_flows/auth.py` are near-identical files (the latter two are
byte-for-byte structural mirrors of each other; memory's differs only in
naming `allowed_contexts` vs `allowed_categories`). This is the same
"duplicate rather than couple" philosophy as `sections.py`'s heading parser:
each service stays independently deployable with zero runtime dependency on
its siblings for something as fundamental as authentication. The cost is
duplicated CRUD/validation code across three files, which is exactly what §4.2
below is meant to fix.

**Cross-cutting ("transversal") tokens are a client-side convenience, not a
server-side concept.** There is no shared token table today — `cerebro token
create` (CLI) generates one secret and registers it independently, via
`value=<secret>` on each service's own `POST /tokens`, so a single credential
works against every service without any of them knowing about the others (see
§2.6 for the partial-failure-safe mechanics).

### 4.2 Planned, not yet implemented: auth unification + user system

**This section describes an agreed design that has not been built yet.**
Recorded in `CLAUDE.md`'s "Work in progress" section (which points to
`luisjdev-pendientes/ecosistema-cerebro` in `cerebro-docs` for the
up-to-date detail — that document is the living source of truth for pending
work, not this manual):

- A single shared `cerebro_auth` Postgres schema, replacing the three
  duplicated `api_tokens` tables, with tokens carrying `allowed_modules` +
  `module_scopes` instead of one scopes/allow-list pair per service.
- Later extended with a `users`/`user_tokens`/`groups`/`user_groups`/
  `group_scopes` system implementing three levels: `user`/`owner`/`admin`.
- **Management operations** (login, creating a user/token/group, backups) will
  live in a new `cerebro-auth` service. **Per-request validation stays local**
  to each service, reading the shared schema directly — explicitly *not* a
  network hop to a central auth service on every request, to avoid making
  every API call depend on a fourth service being up.
- Remote backups via API are blocked on this: knowing which schemas a given
  token/user is allowed to see requires `cerebro_auth` to exist first.

Nothing beyond what's written above is agreed yet — do not build ahead of this
without checking the living design doc first.

---

## 5. `cerebro-flows`: YAML schema and execution protocol

### 5.1 Top-level YAML shape

Validated by `FlowDefinitionSchema` (`schema.py`, pydantic, `extra="forbid"`
throughout via `StrictModel`):

| Field | Type | Notes |
|---|---|---|
| `metadata` | object | `name`, `category`, `description?`, `author?`, `status?` (default `"active"`), `created_at?`, `updated_at?`, `references?` (list of `cerebro-docs://category/slug` URIs — resolved by whoever consumes the step, not by the engine) |
| `input` | object | `required`/`optional`: lists of `{name, type, description?}` |
| `output` | object | `type` (default `"object"`), `properties` (`dict[str, str]`) |
| `entry` | string | id of the first real step (must exist in `procedure`) |
| `procedure` | list of `Step` | the graph itself, see §5.2 |
| `rules` | list | `{id, rule, applies_to: [step ids]}` — free-text guidance scoped to specific steps |
| `tools` | list of string | tool names the model will need; drives the synthetic prerequisites step |
| `execution_policy` | object | `autonomy` (default `"guided"`), `confirmation_required` (list) |

Note that the flow's human-readable sequential id (`code`, e.g. `"INC-22"`) and
its version number are **not** part of this schema — they're assigned by the
server on save (`flow_definitions.code`, auto-incremented per category prefix
unless given explicitly; `flow_definition_versions.version_number`).

### 5.2 Step types

Every `Step` (`{id, type, description?, next?, branches?, terminal?,
checkpoint?, tools_allowed?, delegate?}`) has its shape enforced against its
`type` by a `model_validator`:

| `type` | Requires | Forbids |
|---|---|---|
| `task` | `next` (unless `terminal: true`) | `branches`, `delegate` |
| `decision` | `branches` (non-empty `dict[condition → next step id]`) | `next`, `delegate` |
| `delegate` | the `delegate` block (`{agent_type, prompt_ref?, parallel_group?}`) | `branches` |

`terminal: true` forbids both `next` and `branches` regardless of `type` — a
terminal step is a true dead end in the graph.

A `decision` step is never evaluated by the server — the *caller* reports
which branch was taken (`decision` field in `POST /runs/{id}/next`), and the
server validates it's one of the declared `branches` keys, raising
`InvalidDecisionError` (422) otherwise.

**Parallel delegation**: steps sharing the same `delegate.parallel_group`
value are returned *together* as a list (instead of one step object) when the
engine enters any of them, and referential-integrity validation requires that
every step in a `parallel_group` declares the exact same `next` — i.e. a
parallel fan-out must have one unambiguous rejoin point.

### 5.3 Checkpoints

`Checkpoint = {required: bool (default true), prompt: str, on_reject: str
(target step id)}`. When the *current* step (the last one revealed) has a
`checkpoint` and it hasn't been approved, `POST /runs/{id}/next` fails with
409 (`CheckpointPendingError`) until `approve-checkpoint`/`reject-checkpoint`
is called. Approving sets `pending_checkpoint = {step_id, approved: true}` on
the Redis pointer (consumed and cleared by the next successful `_enter_step`);
rejecting logs `checkpoint_rejected` and immediately transitions to
`checkpoint.on_reject`'s target — it does not require a second `next` call.

### 5.4 Storage split: Redis vs. Postgres

| Store | Holds | Lifetime |
|---|---|---|
| Redis (`flow_run:<run_id>`) | Only the mutable pointer: `current_step_id`, `pending_checkpoint`, `status`, timestamps | Sliding TTL (`FLOW_RUN_TTL_HOURS`, default 72h), refreshed on every transition; deleted immediately on `completed`/`aborted` |
| Postgres (`flow_runs`) | The immutable header: which definition/version started, final status/timestamps | Permanent |
| Postgres (`flow_run_events`) | Append-only, one row **per real transition**, written at the moment it happens | Permanent — this is the audit trail even if Redis's TTL expires on an abandoned run |

The full parsed YAML is **re-read and re-validated from Postgres on every
single call** (`_load_definition`) — it is never cached in Redis, so a
mid-flight run always sees the exact version it started with
(`flow_runs.definition_version`), and a Redis restart/eviction never loses
anything except the convenience of not having to call `flow_start` again (the
run's identity and history survive in Postgres regardless).

### 5.5 The execution protocol

The exact call sequence a caller (a model, via MCP tools, or any other client
of `cerebro-clients`' `FlowsClient`) must follow:

1. **`flow_start(code)`** → `{run_id, status: "in_progress", step}`. Save
   `run_id` — every subsequent call needs it.
2. If the definition declares `tools:`, the **first** `step` returned is
   always the synthetic `{"id": "__prerequisites__", "type": "prerequisites",
   "tools_required": [...]}`. The caller confirms it has those tools (trying
   `ToolSearch` for anything deferred) before calling `flow_next` — if
   something is genuinely missing, it should tell the user and *not*
   continue. There is no dedicated "prerequisites confirmed" call; the caller
   simply calls `flow_next(run_id)` once ready, which advances straight into
   `entry`.
3. **`flow_next(run_id, decision?)`** in a loop to advance. If the step just
   returned was `type: "decision"`, the *next* call must include `decision`
   set to one of its `branches` keys — the server never infers the condition.
   If the current step has an unresolved required checkpoint, this call fails
   (409) until step 4 happens.
4. If a step carries a `checkpoint`, call **`flow_approve_checkpoint(run_id)`**
   or **`flow_reject_checkpoint(run_id, reason)`** before the next
   `flow_next`. Approving unblocks the next `flow_next` call (which then
   proceeds to whatever the step's own `next`/`branches` say). Rejecting
   jumps straight to `checkpoint.on_reject`'s target step and returns it
   immediately — no extra `flow_next` call needed for that transition.
5. `status: "completed"` (with `step: null`) marks a successful end — reached
   automatically when `_enter_step` lands on a step with `terminal: true`.
   There is no free-text "end of flow" signal to recognize.
6. **`flow_abort(run_id, reason?)`** may be called at any point while
   `in_progress`, ending the run irreversibly (`status: "aborted"`) — distinct
   from a rejected checkpoint, which reroutes rather than ends.

| Tool / endpoint | HTTP | Effect on state |
|---|---|---|
| `flow_start` | `POST /flows/{code}/start` | creates `flow_runs` row + Redis pointer, enters prerequisites or `entry` |
| `flow_next` | `POST /runs/{run_id}/next` | advances the Redis pointer to `step.next` (or `branches[decision]`), logs `step_entered`/`decision_taken` |
| `flow_approve_checkpoint` | `POST /runs/{run_id}/approve-checkpoint` | sets `pending_checkpoint.approved=true`, logs `checkpoint_approved` |
| `flow_reject_checkpoint` | `POST /runs/{run_id}/reject-checkpoint` | logs `checkpoint_rejected`, immediately enters `checkpoint.on_reject` |
| `flow_abort` | `POST /runs/{run_id}/abort` | sets `flow_runs.status='aborted'`, logs `aborted`, deletes the Redis key |

---

## 6. The internal gateway (`gateway/`)

`gateway/Caddyfile` + the `gateway` service in `compose.yaml` (image
`caddy:2-alpine`, `full` profile, depends on all three APIs being healthy).
Its only job is path-prefix routing to a single exposed port
(`CEREBRO_GATEWAY_HOST_PORT`, default 8080):

```caddyfile
:80 {
	handle /health {
		respond "cerebro gateway: ok" 200
	}

	handle_path /memory/* {
		reverse_proxy cerebro-memory-api:8000
	}

	handle_path /docs/* {
		reverse_proxy cerebro-docs-api:8000
	}

	handle_path /flows/* {
		reverse_proxy cerebro-flows-api:8000
	}

	handle {
		respond "cerebro gateway: use /memory, /docs or /flows" 404
	}
}
```

`handle_path` **strips the prefix** before forwarding: `/memory/contexts`
reaches `cerebro-memory-api` as `/contexts`; `/docs/categories` reaches
`cerebro-docs-api` as `/categories`; `/flows/flows/INC-1` reaches
`cerebro-flows-api` as `/flows/INC-1` — the repeated `flows` segment there is
that API's own top-level resource (`GET /flows/{code}`), not a routing
mistake.

**Why it exists**: without it, anyone deploying this ecosystem behind their
own reverse proxy (a VPS, someone else's Caddy/nginx) needs one
`reverse_proxy` block per internal service, and that number grows every time a
new module is added. With the gateway, one block
(`reverse_proxy 127.0.0.1:8080`) covers the whole ecosystem regardless of how
many services it has — `CEREBRO_MEMORY_URL=https://your-domain/memory` (same
pattern for `_DOCS_`/`_FLOWS_`) is then enough for `MemoryClient`/
`DocsClient`/`FlowsClient` to work with **zero code changes**, since they
already build final URLs by concatenating `base_url` + a relative path.

It does **not** replace each API's direct host port (those stay reachable for
local dev or direct access inside the compose network) — it's an additional,
optional layer. Any new module added to the ecosystem must add its own
`handle_path` block here, rather than forcing whoever deploys it to touch
their own external reverse proxy config (see §8).

---

## 7. Testing conventions

Every package with an integration suite (`cerebro-memory`, `cerebro-docs`,
`cerebro-flows`, `cerebro-cli`, and transitively `cerebro-clients`) isolates
its tests against an **ephemeral** `cerebro_test` database, dropped and
recreated in a `pytest_configure` hook (`tests/conftest.py`) — deliberately
*not* a fixture, because `api.py` instantiates `app = create_app()` at module
level (needed for `uvicorn <pkg>.main:app` in production), which calls the
`@lru_cache`d `get_settings()` at *import* time, during pytest's collection
phase — before any fixture, no matter how early, gets to run. `pytest_configure`
runs before collection, in time to override `DATABASE_URL`/`REDIS_URL` before
any test module (and therefore `api.py`) is imported. Without a reachable
Postgres/Redis, the integration suites skip cleanly rather than failing.
`cerebro-flows`' variant additionally does `FLUSHDB` on a dedicated Redis
logical database (`/15`).

```bash
cd packages/<package>
pip install -e ".[dev]"
pytest
```

This mechanism, and the CLI/branching/merge-approval workflow rules around it,
are documented in full in `CLAUDE.md` — this manual doesn't duplicate that
content.

---

## 8. Extending the ecosystem: adding a 7th module

Following the four-layer pattern (§1.3), adding a new service — call it
`cerebro-widgets` — looks like:

1. **Scaffold the package**: `packages/cerebro-widgets/` mirroring an existing
   service's layout (`pyproject.toml`, `src/cerebro_widgets/`, `db/migrations/`,
   `tests/`, `Dockerfile`). Give it its own Postgres schema
   (`cerebro_widgets`) — never reuse or cross-reference another service's
   schema.
2. **Migration layer**: `db/migrations/001_init.sql`, following the same
   "unqualified objects land in the right schema because `search_path` is
   already set by `db.py`'s pool creation" pattern as the existing services.
   Include your own `api_tokens` table if this service needs its own
   auth-scoped tokens (until §4.2's unification lands, this is still the
   expected pattern — duplicate `auth.py`, don't share it).
3. **`config.py`**: a `pydantic-settings` `Settings` class mirroring
   `cerebro_docs.config`/`cerebro_flows.config` — `database_url`, `api_token`,
   `app_host`/`app_port`, `migrations_dir`, plus whatever this service
   uniquely needs. Never hardcode a URL/port/token.
4. **`api.py`**: a FastAPI app via `create_app(settings)`, with `/health`
   requiring no auth, every other route behind `require_scope(...)`, and
   scope-appropriate `allowed_*` enforcement if the service has a
   category/context-like isolation concept. **Do not** add `from __future__
   import annotations` if you declare nested `Depends(...)` inside
   `create_app()` (§1.3).
5. **`cerebro-clients`**: add `widgets_client.py` (`WidgetsClient(BaseClient)`)
   with one method per endpoint, plus the corresponding `widgets_base_url()`/
   `widgets_token()` entries in `config.py` (`CEREBRO_WIDGETS_URL`, sharing the
   same `CEREBRO_TOKEN`/`CEREBRO_AGENT_NAME` as the others unless there's a
   specific reason not to).
6. **`cerebro-mcp`**: instantiate `_widgets = WidgetsClient()` in `server.py`
   and add `@mcp.tool()`-decorated `widget_*`-prefixed functions, each calling
   a client method and returning `{"error": "..."}` on any
   `CerebroAPIError`/`CerebroConnectionError` — never let an exception
   propagate to the model. Update the `FastMCP(instructions=...)` string if
   the new tool family changes how a model should decide what to use when.
7. **`cerebro-cli`**: add `widget_commands.py` with an `argparse` subtree
   under a new `widget` subcommand, wired into `main.py`, following the same
   client-call-then-print pattern as `docs_commands.py`/`flow_commands.py`.
8. **`compose.yaml`**: add a `cerebro-widgets-api` service under the `full`
   profile, same shape as the existing three (`depends_on: postgres:
   service_healthy` [+ any other infra it needs], `env_file: .env`,
   `DATABASE_URL` override for the in-network Postgres hostname, a
   `CEREBRO_WIDGETS_HOST_PORT`-configurable port mapping, and a `/health`
   healthcheck).
9. **`gateway/Caddyfile`**: add a `handle_path /widgets/* { reverse_proxy
   cerebro-widgets-api:8000 }` block — this is mandatory for any new module,
   so whoever deploys the ecosystem behind their own reverse proxy never has
   to add a new block on their end (§6).
10. **Tests**: a `tests/conftest.py` with the same `pytest_configure`
    ephemeral-database pattern as the existing services (§7); unit tests for
    anything with pure logic (validators, parsers), integration tests for the
    API behind a real Postgres.
11. **Docs**: add the new package to this manual's per-package section and API
    reference tables, and to `CLAUDE.md`'s module table — keep both in sync
    with what's actually implemented, the same standard this manual was held
    to.

If the new module's auth needs turn out to require cross-service knowledge
(e.g. "can this token see widgets *and* docs"), that's exactly the gap §4.2's
planned `cerebro_auth` unification is meant to close — don't build a one-off
bridge for it in the meantime without checking the living design doc first.
