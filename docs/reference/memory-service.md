# cerebro-memory reference

> Endpoints, Context Engine, optional local classifier, relationships/timeline and evaluation of the memory service. Moved out of the README; see the [documentation index](../README.md).

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
