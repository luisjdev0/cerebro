# cerebro

**Persistent memory, complete documents and step-by-step workflows for AI agents — self-hosted.**

Language models forget everything between conversations. cerebro is a set of small services
you run yourself that give any model (Claude, GPT, Gemini, your own agent) three things it
can use through [MCP](https://modelcontextprotocol.io) or a CLI:

- **Memory** — facts, decisions, events and procedures that survive across sessions, found by
  meaning (vector + full-text search) and scoped automatically to the right project.
- **Documents** — complete Markdown documents (guides, specs, long notes) with categories,
  versioning, partial edits and search; never distilled into fragments.
- **Flows** — processes written in YAML and revealed to the model one step at a time
  ("traffic light"), with decisions, approval checkpoints and parallel delegation, so it cannot
  skip steps.

Everything sits behind one identity system (users, groups, scoped tokens, OAuth for MCP
connectors) and one gateway, so you expose a single port. It is not a single-user product:
clone it, deploy your own instance, change it.

## What's in the box

| Package | What it is |
|---|---|
| [`cerebro-memory`](packages/cerebro-memory) | Memory API: PostgreSQL + pgvector, hybrid retrieval, lifecycle by supersession, Context Engine, relationships and timeline |
| [`cerebro-docs`](packages/cerebro-docs) | Documents API: categories, versions, section patches, archiving, redirects |
| [`cerebro-flows`](packages/cerebro-flows) | Flow engine: YAML workflows, runs, checkpoints, diagrams (Postgres + Redis) |
| [`cerebro-auth`](packages/cerebro-auth) | Users, groups, tokens, password login and the OAuth 2.1 server for MCP connectors |
| [`cerebro-mcp`](packages/cerebro-mcp) | MCP server (`memory_*`, `docs_*`, `flow_*`, `auth_*` tools): stdio, or HTTP at `/mcp` |
| [`cerebro-cli`](packages/cerebro-cli) | The `cerebro` command (interactive menu or plain subcommands) |
| [`cerebro-clients`](packages/cerebro-clients) | Shared thin `httpx` SDK used by the MCP server and the CLI |
| [`cerebro-portal`](packages/cerebro-portal) | Web portal (React): browse and manage documents, flows, memory, users and tokens |

```
Agent / person ──► gateway (Caddy, one port)
                     ├─ /api/memory  /api/docs  /api/flows  /api/auth   (FastAPI services)
                     ├─ /mcp         (MCP over HTTP, OAuth sign-in or token header)
                     ├─ /api/        (Swagger UI)
                     └─ /            (web portal)
                   all services share one PostgreSQL (one schema each) and, for flows, Redis
```

## Run it

You need Docker. This brings up the whole stack behind a single port:

```bash
cp .env.example .env          # change API_TOKEN and POSTGRES_PASSWORD at least
docker compose --profile full up -d --build
curl http://localhost:8080/health
```

To sign in to the portal at `http://localhost:8080`, set `ADMIN_USER` and `ADMIN_PASSWORD` in `.env`
before starting (or create a user with the CLI using the root `API_TOKEN`), then connect a client:

- **Claude Code / scripts (token header):** `claude mcp add --transport http cerebro http://localhost:8080/mcp --header "Authorization: Bearer <token>"`
- **claude.ai, ChatGPT, Cursor and other connectors (no headers):** add `https://<your-domain>/mcp`
  as a connector; it opens a cerebro sign-in page (set `CEREBRO_PUBLIC_URL` and serve it over HTTPS).
- **Terminal:** install the `cerebro` binary from the latest release, then `cerebro login`.

Production deployment (VPS, Caddy, HTTPS, backups, updates) is in [`DEPLOY.md`](DEPLOY.md).
Local development without Docker for the services is in
[`docs/reference/getting-started.md`](docs/reference/getting-started.md).

## Documentation

| If you want to… | Read |
|---|---|
| Use cerebro day to day (memory, docs, flows, CLI, portal) | [`docs/user-manual.md`](docs/user-manual.md) |
| Deploy it on a server | [`DEPLOY.md`](DEPLOY.md) |
| Understand the architecture, API and auth model, or contribute | [`docs/technical-manual.md`](docs/technical-manual.md) |
| Look up a specific feature (Context Engine, security, MCP, CLI, evaluation…) | [`docs/reference/`](docs/README.md) |
| Call the API | Swagger UI at `/api/` on your instance, or the [Postman collection](docs/postman/cerebro.postman_collection.json) |
| Give a model the instructions for using the tools | [`skills/cerebro`](skills/cerebro) |
| Work on the repo (conventions, workflow, tests) | [`CLAUDE.md`](CLAUDE.md) and [`CHANGELOG.md`](CHANGELOG.md) |

## Contributing and license

Work happens on branches and reaches `main` through pull requests; the green criterion is
GitHub Actions (see [`CLAUDE.md`](CLAUDE.md)). Released under the [MIT License](LICENSE).
