# cerebro-memory (`memory_*`)

Distilled memory: facts, preferences, decisions and events reduced to 1–3
self-contained sentences. It is not a conversation archive — it is a curated
summary, meant to be retrieved quickly and to guide reasoning, not to
reconstruct the full conversation.

## Tools

| Tool | Use it to |
|---|---|
| `memory_search` | Retrieve facts/decisions/events before answering. First call by default. |
| `memory_contexts` | See the existing contexts and their descriptions. |
| `memory_create_context` | Create a new context ONLY if no existing one fits. |
| `memory_remember` | Save something new that is worth keeping long-term. |
| `memory_update` | A stored fact changed (new version, keeps history). |
| `memory_forget` | The user asks to forget something, or a memory became obsolete with no replacement. |
| `memory_link` | Connect two existing memories (decision→cause, procedure→project…). |
| `memory_related` | See a memory's 1-hop neighbors (relations + versions). |
| `memory_timeline` | "What happened in X lately?" — events and decisions by date. |
| `memory_stats` | The user wants to see the system's state/learning. |

## Searching: `memory_search`

- Write the query in natural language, as the question you are trying to
  answer.
- **Omit `context` by default**: the Context Engine decides on its own
  (automatic scoping). Pass it only if you already know for certain which
  context it is.
- **If the response carries `ambiguous: true`**: `results` is empty on
  purpose. The `message` field lists the candidate contexts with evidence.
  Decide yourself (using the conversation context) which one applies, or ask
  the user if it truly cannot be deduced, and **repeat the call with
  `context=<slug>`**. That second call, besides searching, teaches the server
  to resolve similar queries on its own in the future — that is why it
  matters to resolve it in the immediately following call and not leave it
  hanging.
- Use `type` to filter ("decision" for "why did we decide X?") and
  `expand=True` when you also want the memories related to the results.
  Careful: `related` are neighbors by relation, not search results — do not
  present them with the same confidence.
- **An empty search ≠ license to invent.** Say that nothing is saved about
  it and, if applicable, offer to save it.

## Saving: `memory_remember`

Save when the user shares something with future value:

- **`semantic`** — stable facts and preferences: "I use Next.js on project
  X", "I prefer answers in Spanish".
- **`episodic`** — dated one-off events: "today the production server went
  down".
- **`procedural`** — how something is done: "deploys are done with `make
  deploy` from main".
- **`decision`** — a decision and its reason: "we chose Postgres because Mongo
  raised prices". Always save the why, not just the what.

Writing rules:

- `context` and `type` are **required**. If you don't know the context, call
  `memory_contexts` first and pick the one that fits best by its description.
  Create a new one with `memory_create_context` only if none truly applies —
  contexts exist to ISOLATE information that must not be mixed (the user's
  personal finances are not a client's finance project), not to collect
  redundant folders.
- Write `content` in 1–3 self-contained sentences: someone without this
  conversation must be able to understand them. Convert relative dates
  ("yesterday", "last week") into absolute ones.
- Before saving something that sounds already known, search first: if an
  equivalent memory exists and the fact changed, it is `memory_update`, not a
  duplicate.
- **Do not save**: ephemeral details of the current conversation, things the
  repo/code already records, or secrets (passwords, API keys, tokens — the API
  rejects them; save a `secret://environment/name` reference if needed).
- If you saved something, tell the user in one line ("saved in `<context>`").
  The memory belongs to the user, not to you: no silent writes.

## Updating, linking or forgetting: choose well

- **The fact changed** (new rate, new version, the plan changed) →
  `memory_update(memory_id, content)`. It creates the new version and marks
  the old one as superseded, keeping history. NEVER create a loose memory that
  competes with the old one in searches.
- **Two current memories are related** → `memory_link` with one of its 5
  relations: `caused_by` (decision→its cause), `part_of` (procedure→its
  project), `follows` (episode→its consequence), `contradicts` (real conflict
  with no clear supersession), `relates_to` (generic association, last
  resort). Golden pattern: when saving a `decision`, link it to its cause's
  memory if one exists.
- **It should no longer appear** → `memory_forget(memory_id)`: archives it
  (recoverable). `hard=True` deletes irreversibly — use it ONLY if the user
  explicitly asks (e.g. something sensitive was saved by mistake) and confirm
  with them first.
