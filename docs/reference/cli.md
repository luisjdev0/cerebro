# CLI reference

> The `cerebro` command-line tool. Moved out of the README; see the [documentation index](../README.md).


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
`[code truncated]` before processing -- a memory is a distilled summary, not a
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
