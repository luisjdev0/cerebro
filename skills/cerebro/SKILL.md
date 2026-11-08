---
name: cerebro
description: Mandatory protocol for using the "cerebro" ecosystem (MCP with persistent memory, complete documents, workflows and access administration). ALWAYS activate it at the start of a conversation, and whenever the answer depends on who the user is, their preferences, projects, past decisions, saved documents or a step-by-step process — even if they don't say "memory", "document" or "flow". Also activate it when the user shares a rememberable fact/preference/decision/event; wants to save/read/edit/archive a document; wants to run, resume or create a step-by-step process; asks to remember/forget/update something; asks what you know about them; or asks to create/manage a cerebro user, group or token. If you are about to claim that you don't know the user, their context, one of their documents or an ongoing process, this skill applies and requires you to query cerebro first, never to assume.
---

# Cerebro: memory, documents, flows and access administration

"Cerebro" is the user's long-term memory, document repository and process
engine. Conversations are lost between sessions; cerebro is not. That is why
the source of truth about the user, their projects, their past decisions,
their documents and their ongoing processes is cerebro, never your
conversation context or your assumptions.

Cerebro has three knowledge modules with distinct, complementary purposes,
loosely coupled to each other (one module can reference another by URI or
code, but there is never direct cross-access to their data):

- **cerebro-memory** (`memory_*`) — distilled memory: facts, preferences,
  decisions and events reduced to 1–3 sentences ("memory over conversation").
  Optimized to be retrieved quickly and to guide reasoning.
- **cerebro-docs** (`docs_*`) — **complete** Markdown documents, never
  truncated: guides, specifications, meeting minutes, runbooks, any text that
  must be kept whole. They do not count as formal memory and are not
  distilled.
- **cerebro-flows** (`flow_*`) — processes with steps, decisions and approval
  checkpoints, defined in YAML. The server reveals the process **step by
  step**, never the full definition at once — so it is impossible to skip a
  checkpoint that has not been seen yet. You (the model) keep performing the
  real actions with your own tools; cerebro-flows only decides which step is
  next and when. You can also **create** new flows, not just run them.

A fourth module, different in nature from the three above:

- **cerebro-auth** (`auth_*`) — it does not store knowledge about the user, it
  manages **access to the ecosystem itself** (users, groups, tokens). Use it
  only when the user explicitly asks to create or manage a user, a group or a
  token — never on your own initiative, and never as part of the
  conversation-start protocol below (see `references/auth.md`).

## Rule zero: remember before assuming

**You must not claim that you don't know the user, their context, their
history, one of their documents, or the state of an ongoing process without
having queried cerebro first.** Any statement of ignorance — or asking the
user for information cerebro might have — is only valid AFTER a query that
came back empty, and in that case it is presented as a result: "I searched
your memory/documents and found nothing about X".

The symmetric rule also applies: **you must not invent memories, documents or
process steps.** Never attribute to the user a fact, document or decision
"from a previous session" that does not come from a real cerebro tool result,
and never invent which step comes next in a flow — only `flow_next` says
that. If it is not in cerebro, you do not "remember" it or "suppose" it.

## Conversation-start protocol

1. If the request may depend on prior context (who the user is, what they
   work on, what they prefer, what was decided before), call `memory_search`
   with a natural-language query BEFORE answering.
2. If the request points to a complete document (a guide, minutes, a README,
   something the user "saved" or "wrote" earlier), use `docs_search` (fuzzy
   text) or `docs_get` (exact path if you already know it).
3. If the request points to a process with steps (an onboarding, a checklist,
   an incident already being worked on), check whether a definition already
   exists (`flow_list`/`flow_get`) or a `run_id` is in progress before
   improvising the steps yourself.
4. If it is the first time you work with this user, or you don't know how they
   organize their knowledge, call `memory_contexts`, `docs_categories` and/or
   `flow_categories` before saving anything new.
5. Only if the relevant queries come back empty, treat the user or the topic
   as new — and say so as the result of the query, not as an assumption.

No query is needed for self-contained requests that depend on nobody in
particular ("what is 2+2?", "explain what a mutex is"). When in doubt, query:
one extra query is cheap; a wrong assumption is not.

## Memory, document or flow?

| If the user... | Use |
|---|---|
| Shares a fact, preference, decision or event in 1-3 sentences | `memory_remember` (see `references/memory.md`) |
| Asks to save/read/edit a long, complete text (guide, spec, minutes, runbook) | `docs_save`/`docs_get`/`docs_update` (see `references/docs.md`) |
| Wants to run or resume a process with steps/decisions/approvals | `flow_start`/`flow_next` (see `references/flows.md`) |
| Wants to define a new process (checklist, runbook with formal steps) to run it later | `flow_validate` → `flow_save` (see `references/flows.md`) |
| Asks "what do you know about me / this project?" | `memory_search` (and `docs_search` if a relevant document might exist) |
| Asks "where did the guide/document about X end up?" | `docs_search` or `docs_list` |
| Asks "which step did the process for X stop at?" | `flow_get`/state of the `run_id` (see `references/flows.md`) |
| Wants a fact to point to a document without duplicating its content | `memory_remember` with `cerebro-docs://category/slug` in the `content` |
| Asks to create/manage a user, group or access token for cerebro itself | `auth_*` (see `references/auth.md`) — different from the rows above: it is not user knowledge, it is access control |

## References by module

The detail of each module (tool tables, when to use each one, write rules,
examples) lives in a separate file — read it when the task enters that module;
there is no need to load them all at once:

- **`references/memory.md`** — search/save/update/link/forget memories
  (`memory_*`).
- **`references/docs.md`** — search/save/edit/archive documents, hidden
  categories, path redirects (`docs_*`).
- **`references/flows.md`** — run a flow step by step AND create/edit new
  definitions, including the YAML format (`flow_*`).
- **`references/auth.md`** — create/manage users, groups and access tokens for
  the ecosystem itself (`auth_*`), only when the user explicitly asks.

## Errors and honesty

- If a tool returns `error` (API down, invalid token, nonexistent
  context/category), report it to the user as is, along with the action the
  message suggests. **A connection error does not authorize you to answer
  "from memory"**: say that cerebro is not available right now.
- If a response carries an `alert` (e.g. `docs_get` warning that a path was
  moved), treat it as an actionable instruction, not a footnote: stop using
  the old path from then on and fix it in any memory or document where you had
  it saved.
- Always distinguish the three situations in your answer: (a) I found it in
  cerebro, (b) I queried and nothing is saved, (c) I could not query cerebro.
  Never present (b) or (c) as if it were knowledge.

## Operating summary

1. Could it depend on the past? → `memory_search` first. Could a relevant
   complete document exist? → add `docs_search`/`docs_get`. Is it or could it
   be part of a process with steps? → add `flow_list`/`flow_get`. No
   exceptions for questions about the user, their history, their documents or
   their processes.
2. Ambiguous memory result? → resolve with `context=<slug>` in the next call
   (see `references/memory.md`).
3. Something new with future value? Short fact/decision → `memory_remember`.
   Complete document → `docs_save`. New process with steps → write the YAML,
   validate it with `flow_validate`, and save it with `flow_save`. Tell the
   user in all three cases.
4. Did something change? → `memory_update` / `docs_update` (or
   `docs_patch_section` for just one section) / `flow_update`. Are two
   memories related? → `memory_link`. Is something left over? →
   `memory_forget`/`docs_archive` (reversible, prefer them) or
   `docs_delete`/`flow_delete` (irreversible, with prior confirmation).
5. Is there an ongoing process? → follow it with `flow_next` step by step,
   never by reading the full definition to "guess" the next step.
6. Never invent memories, documents or process steps; never declare ignorance
   without having queried cerebro first.
7. Did the user explicitly ask to create/manage a user, group or token? → that
   is the only entry point to `auth_*` (see `references/auth.md`) — never on
   your own initiative, and never as part of steps 1-6 above.
