# Security

> Tokens and scopes, restrictions by context/category, agent identity, secrets, validation, encryption and backups. Moved out of the README; see the [documentation index](../README.md).


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
