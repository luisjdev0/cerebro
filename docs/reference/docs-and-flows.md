# cerebro-docs and cerebro-flows reference

> Versioned Markdown documents and the traffic-light flow engine. Moved out of the README; see the [documentation index](../README.md).

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
| `GET` | `/flows/{code}/diagram` | read | Mermaid flowchart of the complete definition (`format=mermaid`, `version?`) -- for people; execution still reveals one step at a time |
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
