# Cerebro User Manual

Cerebro is a self-hosted "second brain" you use together with an AI assistant. It
gives the assistant a place to keep things about you and your work across
sessions — so you don't have to re-explain yourself every time you open a new
chat — and a way to run multi-step procedures without skipping steps.

This manual is for someone who already has (or will have) a running cerebro
instance, and wants to actually *use* it: through an AI assistant like Claude
Desktop or Claude Code (connected via MCP), or through the `cerebro`
command-line tool.

- Setting up the server itself (Docker, Postgres, the VPS, reverse proxy) is
  covered in `DEPLOY.md` — this manual only covers connecting a *client* to an
  instance that's already running.
- The internal architecture (database schemas, HTTP API routes, the Context
  Engine's algorithm, the flows state machine) is covered in the companion
  Technical Manual. This manual stays at the "how do I use it" level.

---

## 1. What cerebro is and isn't

Cerebro stores three different kinds of content, on purpose kept separate so
each stays good at its job:

| | Memory | Docs | Flows |
|---|---|---|---|
| What it holds | Short, distilled facts, preferences, decisions and events (1–3 sentences each) | Complete Markdown documents, never summarized or truncated | Step-by-step procedures with decisions and approval checkpoints |
| Written by | The assistant, summarizing what you told it | You (or the assistant, dictating the full text) | You (or the assistant), as a YAML definition |
| Read by | The assistant, via fast search, to recall who you are and what you've decided | The assistant, on demand, when it needs the full text of something | The assistant, one step at a time, while actually running a process |
| Analogy | A friend's mental notes about you | A shared filing cabinet | A checklist that only shows you the next box after you've ticked the current one |

### Which one do I want?

- *"Remember that I prefer replies in Spanish"* / *"We decided to use Postgres
  because Mongo raised prices"* / *"Yesterday the production server went
  down"* → **memory**. Short, will get summarized to a few sentences.
- *"Save this deployment guide"* / *"Here's the full meeting minutes, keep
  them"* / *"What does our onboarding doc say, word for word?"* → **docs**.
  Anything where the exact wording matters and nothing should be cut.
- *"Run the incident-response process"* / *"Walk me through onboarding a new
  client, step by step"* → **flows**. Anything with an order of operations,
  branching decisions, or a point where you want to approve before the
  assistant continues.

A useful rule of thumb: if you'd be upset that the assistant "helpfully"
paraphrased it, it belongs in **docs**. If you just want the assistant to
*remember* it happened or was decided, **memory** is enough — and it's
cheaper to recall later. If it has steps that should happen in order, with a
point where you want to sign off before continuing, it's a **flow**.

Memory can also point at a doc instead of duplicating it — the assistant may
save a short memory like "the deploy runbook lives at
`cerebro-docs://ops/deploy-runbook`" instead of copying the runbook's content
into memory.

---

## 2. Getting connected

Cerebro itself is a set of small HTTP services running on a server (yours, or
one someone set up for you) — see `DEPLOY.md` for how that part is installed
and configured. This section is about pointing a *client* at an
already-running instance.

There are two ways to talk to a running cerebro instance:

### A. Through an AI assistant (MCP)

`cerebro-mcp` is a single MCP server that exposes all three modules
(`memory_*`, `docs_*`, `flow_*` — 36 tools total) over stdio. It runs on
**your** machine (e.g. inside Claude Desktop or Claude Code) and talks to the
remote cerebro APIs over HTTPS. You don't call these tools yourself — you
just talk to the assistant normally, and it decides when to use them (it
follows a "cerebro" skill/protocol that tells it to check memory/docs/flows
before claiming it doesn't know something about you).

To wire it up, whoever manages your Claude Desktop config adds an entry
along these lines (Windows path shown; adjust for your OS) in
`claude_desktop_config.json`:

```json
"cerebro": {
  "command": "C:\\path\\to\\cerebro\\.venv\\Scripts\\cerebro-mcp.exe",
  "env": {
    "CEREBRO_MEMORY_URL": "https://your-cerebro-host/memory",
    "CEREBRO_DOCS_URL": "https://your-cerebro-host/docs",
    "CEREBRO_FLOWS_URL": "https://your-cerebro-host/flows",
    "CEREBRO_TOKEN": "<your personal/agent token, not the root token>",
    "CEREBRO_AGENT_NAME": "claude-desktop"
  }
}
```

- All three URLs must be set explicitly — none of them falls back to
  something useful for a remote server (only to a local-development
  default), so a missing one usually means the assistant silently tries to
  reach `localhost` and fails.
- `CEREBRO_TOKEN` identifies *you* (or this particular assistant install) to
  the server — see "Managing your access" below for how to get one.
- `CEREBRO_AGENT_NAME` is just a label that shows up in cerebro's audit log
  (e.g. so you can tell "claude-desktop" wrote a memory apart from
  "claude-code").

Once that's in place, restart Claude Desktop/Code and just talk to it — ask
it something only cerebro would know ("what did we decide about the
database?") to confirm the connection works.

### B. Through the `cerebro` CLI

For direct or scripted use (backups, bulk imports, managing tokens, editing a
flow definition by hand) there's a companion `cerebro` command-line tool. It
reads the same kind of environment variables:

```bash
export CEREBRO_MEMORY_URL=https://your-cerebro-host/memory
export CEREBRO_DOCS_URL=https://your-cerebro-host/docs
export CEREBRO_FLOWS_URL=https://your-cerebro-host/flows
export CEREBRO_TOKEN=<your token>

cerebro docs list
```

(On Windows/PowerShell use `$env:CEREBRO_TOKEN = "..."` instead of `export`.)
See section 6 below for the full command reference.

---

## 3. Using memory day to day

You never call memory tools directly — you just talk, and the assistant
decides what's worth keeping. That said, it helps to know what's actually
happening under the hood so you can tell it what you want.

### The four kinds of memory

- **Semantic** — a stable fact or preference. *"I use Next.js on the
  marketing site project."* *"I prefer replies in Spanish."*
- **Episodic** — something that happened, at a point in time. *"On September
  20th the production server went down for about two hours."*
- **Procedural** — how something is done. *"Deploys are done with `make
  deploy` from `main`."*
- **Decision** — a choice, and (importantly) *why*. *"We chose Postgres over
  MongoDB because Mongo's pricing changed."* A decision without its reason
  isn't saved as a decision — the assistant should always capture the "why."

You'll notice the assistant condenses what you say into 1–3 self-contained
sentences, with relative dates ("yesterday") turned into absolute ones. That's
intentional: memory is meant to be fast to search later, not a transcript of
the conversation.

### Contexts, and why the assistant sometimes asks "which one?"

Memories live in **contexts** — separate buckets so things that shouldn't mix
don't (your personal finances vs. a client's project finances, for example).
When you say something and it's genuinely unclear which context it belongs
to, the assistant's search can come back "ambiguous" instead of guessing —
you'll see it ask something like *"Is this about the Acme project or your own
freelance work?"* Answering once teaches the system to resolve similar
questions on its own next time, so this should get rarer over time in a given
area, not more frequent.

You can also just ask *"what contexts do you have for me?"* if you want to
see how your knowledge is organized.

### Updating, forgetting, and linking

- **Something changed** ("actually we moved to a bigger VPS") → ask the
  assistant to update it. It keeps the old version in history rather than
  leaving two competing memories around.
- **Something should stop being remembered** — ask the assistant to forget
  it. By default this is a soft delete (recoverable); only ask for a
  permanent/hard delete if you specifically need something sensitive
  (accidentally stored) gone for good, and expect the assistant to confirm
  before doing that.
- **Two memories are related** (a decision and the event that caused it, a
  procedure and the project it belongs to) — the assistant can link them so
  that browsing one surfaces the other.

If you ask "what do you know about me?" and the assistant says it searched
and found nothing, that's an honest empty result, not the assistant being
lazy — see the Troubleshooting section.

---

## 4. Using docs day to day

Docs are for anything where you want the **exact text** kept — guides,
meeting notes, runbooks, specs. Nothing here gets summarized.

### Creating and reading

Just ask: *"Save this as a doc"* (paste or dictate the content), or *"Save
our meeting notes from today under the `meetings` category."* The assistant
needs a **category** for anything it saves — if you don't specify one, it
will check what categories already exist and either use one that fits or ask
you (or, rarely, propose a new one). Each document lives at a path like
`/category/slug` (e.g. `/ops/deploy-runbook`).

To read something back: *"What does the deploy runbook say?"* or *"Show me
the onboarding doc"* — the assistant will search by title/content if you
don't know the exact path, or fetch it directly if you do.

You can also ask for a document to be updated in full (*"replace the
runbook with this new version"*) or edited in just one section (*"update the
'Rollback' section of the runbook to say X"*) without touching the rest of
the document.

### Categories: archived, hidden, and locked

- **Archived** documents are the "soft delete" of docs — they stop showing up
  in normal lists/searches, but nothing is lost, and you (or the assistant)
  can bring one back by asking. A true, permanent delete is a separate,
  deliberate action the assistant should only do when you've clearly asked
  for it, since it removes the document's entire history for good.
- **Hidden** categories don't show up when you ask "what categories exist?"
  or in ordinary listings — but a document inside one can still be opened if
  you (or the assistant) know its exact path. This is mostly used for
  internal/reference material that isn't meant to clutter your normal view,
  not something you'd typically ask for yourself.
- **Locked** additionally means a hidden category can never be made visible
  again. This is rare and deliberate — if the assistant is about to lock a
  category, it should be sure, since there's no tool to undo it.

### When a document moved: redirects

If a document's category or slug is renamed, the old path doesn't just break.
Ask for it by the old path and the assistant will still find it — but it will
tell you it moved and give you the new location, something like: *"Note: this
document used to live at `/guides/deploy` — it's now at
`/ops/deploy-runbook`."* That's not a bug notice, it's the assistant doing
its job: it should also stop using the old path afterward, including fixing
any memory or other document where the old path was written down. If you see
that message, you don't need to do anything except start using the new path
yourself.

---

## 5. Using flows day to day

A flow is a defined, multi-step procedure — an incident checklist, an
onboarding process, anything with a fixed sequence, branching decisions, or a
point where you want to approve before the assistant keeps going.

### What it feels like when a flow runs

You (or the assistant) start a flow by name, and from that point on the
assistant only ever sees **one step at a time** — never the whole procedure
up front. This is deliberate: it's not that the assistant is being asked
nicely not to skip ahead, it *cannot* see step 5 until step 4 is done. So
when you're watching a flow run, expect it to narrate one step, do the work
for that step with its normal tools, and then ask for the next one — not to
dump the whole plan on you at once.

Some steps are decisions: the assistant tells you (or figures out from what
just happened) which branch applies, and the flow continues down that path.

### Checkpoints

Certain steps are marked as **checkpoints** — the flow deliberately stops and
waits for your explicit approval before continuing. This is usually placed
right before something consequential (e.g. actually creating a ticket,
deploying something, or closing an incident). The assistant should ask you in
plain language before approving a checkpoint, the same way it would before
any other hard-to-undo action.

If you say no, the flow doesn't just stop — it's routed back to whatever
earlier step the flow's author designated (often "go back and investigate
more"), so rejecting a checkpoint is a redirect, not a cancellation.

### Abandoning a flow

If you decide partway through that you don't want to continue a process at
all, just tell the assistant to stop/abandon it. That's different from
rejecting a checkpoint (which loops back) — abandoning ends the run for good
and is recorded as such. An abandoned or simply-forgotten flow doesn't hang
around forever either way: an inactive run eventually expires on its own, but
if you already know you're not continuing, saying so explicitly leaves a
cleaner record than just walking away from it.

---

## 6. The `cerebro` CLI

The CLI is organized as `cerebro <module> <subcommand>`, plus a few
cross-cutting commands with no module prefix. It's a thin wrapper over the
same HTTP APIs the assistant uses — useful for scripting, bulk operations,
and anything administrative that doesn't need a conversation.

Note the asymmetry: day-to-day memory use (searching, remembering, updating,
forgetting) is only exposed through the assistant/MCP, not the CLI — the CLI
covers memory administration (stats, bulk import, tokens), while **docs** and
**flows** have full CRUD available from the CLI as well, since those are
often edited directly as files.

### `cerebro memory ...`

| Command | What it does |
|---|---|
| `cerebro memory stats` | System statistics (same as the API's `/stats`). |
| `cerebro memory export-disambiguations [--output FILE] [--resolved-only]` | Exports the context-disambiguation log to JSONL. |
| `cerebro memory import-markdown <path> --context SLUG [--type TYPE] [--dry-run] [--create-context] [--context-description TEXT]` | Bulk-imports memories from existing Markdown files (a file or a directory, recursively). |
| `cerebro memory token create <name> --scopes read,write[,admin] [--contexts slug1,slug2]` | Creates a token valid for cerebro-memory only; prints the secret once. |
| `cerebro memory token list` | Lists memory tokens (no secrets shown). |
| `cerebro memory token revoke <name>` | Revokes a memory-only token by name. |

### `cerebro docs ...`

| Command | What it does |
|---|---|
| `cerebro docs category create <slug> [--name NAME] [--description TEXT] [--hidden] [--locked]` | Creates a category. `--locked` requires `--hidden` and is permanent. |
| `cerebro docs category list` | Lists categories. |
| `cerebro docs category rename <slug> <new_slug> [--name] [--description] [--hidden \| --visible]` | Renames/edits a category (pass the same slug twice to only change name/description/visibility). |
| `cerebro docs category delete <slug> [--force]` | Deletes a category; fails if it has documents unless `--force` (cascades). |
| `cerebro docs save <category> <title> [--content-file FILE] [--slug SLUG]` | Saves a brand-new document (reads content from stdin if no `--content-file`). Fails if the slug already exists in that category. |
| `cerebro docs get <category> <slug>` | Reads a document by its exact path. |
| `cerebro docs list [--category SLUG] [--archived] [--limit N] [--offset N]` | Lists documents (most recent first). |
| `cerebro docs search <query> [--category SLUG] [--limit N] [--offset N]` | Full-text search over title + content. |
| `cerebro docs update <document_id> <title> <category> [--content-file FILE] [--slug SLUG]` | Full replacement of an existing document (can move it to another category). |
| `cerebro docs patch-section <document_id> <heading> <replace\|append\|insert_after\|insert_before\|delete> [--body TEXT \| --body-file FILE] [--create-if-missing] [--new-heading-level N]` | Partial, section-only edit by heading. |
| `cerebro docs delete <document_id> [--yes]` | Permanently deletes a document and its whole version history. |
| `cerebro docs archive <document_id>` / `cerebro docs unarchive <document_id>` | Soft-delete / restore. |
| `cerebro docs history <document_id>` | Lists a document's previous versions. |
| `cerebro docs import-markdown <path> --category SLUG [--dry-run] [--update]` | Bulk-imports whole documents from Markdown files. |
| `cerebro docs stats` | Categories/documents/versions statistics. |

### `cerebro flow ...`

| Command | What it does |
|---|---|
| `cerebro flow category create <slug> <CODE> [--name] [--description]` | Creates a flow category; `CODE` is the short uppercase prefix used in flow ids (e.g. `INC` → `INC-1`, `INC-2`...). |
| `cerebro flow category list` | Lists flow categories. |
| `cerebro flow validate --yaml-file FILE` | Validates a flow YAML definition without saving it. |
| `cerebro flow save <category> --yaml-file FILE [--code CODE]` | Saves a new flow definition. |
| `cerebro flow get <code>` | Reads a flow's full YAML by its code. |
| `cerebro flow list [--category SLUG] [--limit N] [--offset N]` | Lists flow definitions. |
| `cerebro flow update <code> --yaml-file FILE` | Replaces a flow's YAML (new version; runs already in progress keep the version they started with). |
| `cerebro flow delete <code> [--yes]` | Permanently deletes a flow definition and its run history. |
| `cerebro flow stats` | Categories/flows/runs statistics. |

Actually *running* a flow step by step is done by a model via MCP
(`flow_start`/`flow_next`/etc.), not from the CLI — the CLI is for authoring
and managing definitions.

### Cross-cutting commands

| Command | What it does |
|---|---|
| `cerebro token create <name> --scopes read,write[,admin] [--contexts slugs] [--categories slugs]` | Creates one cross-service token (prefixed `cbr_`), registered on every service that supports it in a single operation. Prints the secret once — save it as `CEREBRO_TOKEN`. |
| `cerebro token revoke <name>` | Revokes a cross-cutting token everywhere it's registered. |
| `cerebro backup [--output DIR]` | `pg_dump` via `docker compose`, covering both the memory and docs schemas. |
| `cerebro restore <file> [--yes]` | Restores a backup produced by `cerebro backup`. **Destructive** — asks for confirmation unless `--yes`. |

If `cerebro token create` succeeds on one service but fails on another
(partial failure), the CLI reports it per-service and exits with an error;
re-running the same command is safe and reuses the same secret rather than
creating a duplicate.

---

## 7. Managing your access

Cerebro authenticates every request with a **token**, and every token carries
one or more **scopes**:

- **`read`** — search and fetch memories/docs/flows.
- **`write`** — create and update them (remember, save, patch, run flow
  steps, etc.).
- **`admin`** — manage tokens and other administrative operations. Reserve
  this for whoever administers the instance, not for a day-to-day assistant
  connection.

A token can also be scoped to specific **contexts** (for memory) or
**categories** (for docs) — meaning it can only see/touch that slice of your
data, rather than everything. This is how you'd set up, say, a
client-specific integration that shouldn't be able to read your personal
notes.

### How do I get one?

You don't create your own token from inside a chat with the assistant — a
token is created by whoever administers your cerebro instance (you, or
whoever set it up for you), using the CLI with the **root** token from the
server's `.env`:

```bash
# One token, registered on every module that supports it:
cerebro token create claude-desktop --scopes read,write

# Or a token scoped to one module only:
cerebro memory token create claude-desktop --scopes read,write
```

The secret is printed **once** — save it somewhere safe (a password manager),
because it can't be retrieved again later, only revoked and recreated.
Day-to-day, you'd use a `read,write` token for your assistant connection, and
keep `admin`-scoped tokens (and the root token) for actual administration.

### What can mine do?

If you're not sure what a token you're using is scoped to, ask whoever set it
up, or (if you have CLI access) list tokens with `cerebro memory token list`
(the value itself is never shown again, only its name and scopes). If the
assistant reports a permission-denied error when trying to do something (e.g.
save a memory with a `read`-only token), that's this scoping working as
intended — the fix is to get a token with the right scope, not to work around
it.

---

## 8. Troubleshooting / FAQ

**"The assistant says it doesn't know me / has no memory of this."**
This usually means it searched and genuinely found nothing — not that
something is broken. Cerebro's protocol specifically forbids the assistant
from claiming ignorance *without* searching first, and forbids it from
inventing a memory that isn't really there. So an honest "I checked and
there's nothing saved about that" is the system working correctly, not a
bug. If you know you told a *different* assistant/session about it, remember
that only things explicitly saved to cerebro persist — a fact mentioned in
passing in a chat that was never "remembered" isn't retrievable later.

**"A document link says it moved."**
That's a redirect alert (see section 4) — the document was renamed or moved
to a different category, and cerebro is telling you (via the assistant)
where it lives now instead of just failing. It's informational, and the
assistant should already be updating any stale references it finds — you
don't need to fix anything yourself.

**"A flow won't let me skip ahead / the assistant won't tell me what's coming next."**
That's by design (see section 5): the server only reveals one step at a
time, on purpose, so a checkpoint can't be worked around by reading ahead.
If you want to know the overall shape of a flow before running it, ask
whoever manages your instance to show you the flow's definition (`cerebro
flow get <code>`) — that's a separate, deliberate "look at the whole
recipe" action, distinct from actually running it.

**"The assistant asked me which project/context something belongs to."**
That's the Context Engine declining to guess when it's genuinely ambiguous,
rather than silently filing something under the wrong bucket. Answer it
once and similar questions in that area should stop coming up.

**"I got a permission/scope error."**
Your token doesn't have the scope needed for that action (see section 7).
Ask whoever administers your instance for a token with the right scope.

**"The assistant can't reach cerebro at all."**
Check that all three URL environment variables are set for your MCP client
(`CEREBRO_MEMORY_URL`, `CEREBRO_DOCS_URL`, `CEREBRO_FLOWS_URL`) — none of
them fall back to a working default for a remote server — and that
`CEREBRO_TOKEN` is set and hasn't been revoked. If it still fails, that's a
deployment/connectivity issue; see `DEPLOY.md`.
