# Connecting to Claude (MCP server)

> Stdio and HTTP MCP, Claude Code and Claude Desktop configuration. Moved out of the README; see the [documentation index](../README.md).


`packages/cerebro-mcp/src/cerebro_mcp/server.py` exposes **all three** services as a
single stdio MCP server (official `mcp` SDK, `FastMCP`). It's a thin adapter:
each tool calls the corresponding HTTP API via `cerebro_clients` (`MemoryClient` /
`DocsClient` / `FlowsClient`), with no business logic of its own -- all of it lives in the APIs,
so `cerebro-cli` shares exactly the same path.

46 tools available:

- **`memory_*`** (12, talk to `cerebro-memory`): `memory_search`,
  `memory_remember`, `memory_update`, `memory_forget`, `memory_contexts`,
  `memory_create_context`, `memory_update_context`, `memory_delete_context`, `memory_stats`, `memory_link`, `memory_related`,
  `memory_timeline`.
- **`docs_*`** (13, talk to `cerebro-docs`): `docs_create_category`,
  `docs_categories`, `docs_save`, `docs_get`, `docs_search`, `docs_list`,
  `docs_update`, `docs_patch_section`, `docs_delete`, `docs_archive`,
  `docs_unarchive`, `docs_list_archived`, `docs_history`.
- **`flow_*`** (13, talk to `cerebro-flows`): `flow_create_category`,
  `flow_categories`, `flow_validate`, `flow_save`, `flow_get`, `flow_diagram`, `flow_list`,
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

### MCP over HTTP (`/mcp`)

Besides the stdio server below (one process per person, token in the environment), the gateway
serves the same tools over **streamable HTTP at `/mcp`** (the `cerebro-mcp` container, `full`
profile). It is stateless and every request carries a token, which it forwards to the APIs, so the
assistant can do exactly what the token allows. No secret lives in that container. There are two
ways to get that token there:

- **Sign in through the connection (claude.ai, ChatGPT, Claude Desktop, Cursor, any MCP client that cannot set
  headers).** Add a custom connector with the URL `https://your-domain/mcp` and nothing else. The
  client discovers the OAuth sign-in from the `401` of `/mcp`, opens the cerebro sign-in page and,
  once you confirm, registers the token itself. If you already have the portal open in that
  browser the page shows your user with a **Confirm connection** button; otherwise it asks for
  username and password (the same as the portal, master user included). The access token is a
  normal 12-hour session; the client renews it with a refresh token (90 days, rotated on every use).
  Disabling the user ends the connection at the next renewal; revoking the connection's access token, logging out its session or changing the password
  ends it at once (the refresh token is tied to the session it was issued with). It is plain OAuth 2.1 (discovery, dynamic client registration, PKCE), so it works with any provider that supports remote MCP, not only Claude. Set `CEREBRO_PUBLIC_URL` in `.env` to the public address (see DEPLOY).
- **A token in a header (Claude Code, scripts).** `Authorization: Bearer <token>` on every request:

```bash
# Claude Code
claude mcp add --transport http cerebro https://your-domain/mcp \
  --header "Authorization: Bearer $CEREBRO_TOKEN"
```

Use the stdio server instead when you work against APIs that are not behind the gateway.

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
