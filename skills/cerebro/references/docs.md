# cerebro-docs (`docs_*`)

**Complete** Markdown documents, never truncated or distilled: guides,
specifications, meeting minutes, runbooks, any text that must be kept whole.
They do not count as formal memory — unlike `memory_remember`, here you never
summarize or trim the content.

## Tools

| Tool | Use it to |
|---|---|
| `docs_search` | Search documents by text (title + content) for a fuzzy reference ("the deployment guide"). Never includes archived ones. |
| `docs_get` | Read a complete document when you already know its exact path `/{category}/{slug}`. Works the same for archived documents and hidden categories. |
| `docs_list` | Explore which documents exist, in one category or across the whole repository. Never includes archived ones. |
| `docs_list_archived` | List archived documents (to unarchive them or decide whether to delete them for good). |
| `docs_categories` | See the existing categories and their descriptions. Call it before `docs_save` if you don't know which one to use. Never includes hidden categories. |
| `docs_create_category` | Create a new category ONLY if no existing one fits. Accepts `hidden`/`locked` (see below). |
| `docs_save` | Save a **new** Markdown document, complete, undistilled. Fails if the slug already exists in that category — it never overwrites silently. |
| `docs_update` | COMPLETE replacement of an existing document (it can move it to another category). Archives a snapshot of the previous content before replacing. |
| `docs_patch_section` | PARTIAL patch by section (heading → next heading of equal or higher level). Operations: `replace`, `append`, `insert_after`, `insert_before`, `delete`. The `heading` must match (with or without the `#` prefix) and be unique, or it fails. |
| `docs_archive` / `docs_unarchive` | Take a document out of circulation without losing it (reversible soft-delete) / revert it. Prefer this over `docs_delete` almost always. |
| `docs_history` | Read a document's version history (full content per version). Read-only, no automatic restore — if you need to recover an old version, copy its content and save it with `docs_update`. |
| `docs_delete` | Deletes a document and its whole version history. **Irreversible** — confirm with the user if in doubt, and consider `docs_archive` instead if the real intent is "get it out of the way", not lose it forever. |

## Saving and editing: `docs_save` / `docs_update` / `docs_patch_section`

- `docs_save` stores the document **whole**, as is — do not summarize or trim
  it. `category` is required and must exist: check with `docs_categories()`
  and create a new one with `docs_create_category` only if none fits.
- If the `slug` already exists in that category, `docs_save` fails instead of
  overwriting. To edit an existing document use `docs_update` (full
  replacement, with an automatic snapshot of the previous content) or
  `docs_patch_section` (surgical edit of a single section by `heading`).
- Before saving a document that sounds like it already exists, search first
  with `docs_search` or `docs_get` — just like with memory, avoid duplicates
  competing with each other.
- If you saved, updated or deleted a document, tell the user in one line with
  its path (`/<category>/<slug>`).

## Archive instead of deleting

`docs_archive`/`docs_unarchive` are cerebro-docs' equivalent of
`memory_forget`: an archived document disappears from `docs_list`/
`docs_search` but stays reachable by its exact path (`docs_get`) and can be
reverted at any time. Prefer it over `docs_delete` unless the user explicitly
asks to delete it forever — `docs_delete` has no trash or soft-delete, it is
the most destructive operation in all of cerebro, treat it with the same
caution as an `rm -rf`. If you are unsure which of the two the user wants,
confirm before calling either.

## Hidden and locked categories

`docs_create_category` accepts two optional flags for categories that must not
show up in `docs_categories()`/listings without an exact slug:

- `hidden=True` — the category does not appear in listings, but it is still
  reachable by creating/reading documents with its exact slug. Useful for
  internal reference documents (e.g. a flow's subagent prompts) that should
  not appear mixed in with the user's normal content. It is reversible: you
  can reveal it later.
- `locked=True` (requires `hidden=True`) — hides it FOREVER, with no way to
  revert it (no tool exists for that). Use it only when you are sure that
  category should never become visible.

Do not invent hidden categories on your own unless the case clearly calls for
it — most of the user's categories should be visible.

## If a path moved: redirects

If you rename a document or a category, the old path does not break silently:
`docs_get` still resolves it, but the response carries a plain-text `alert`
saying it was moved and giving the new path. **Treat that `alert` as an
instruction, not an informational notice**: stop using the old path from then
on, and if you had it saved in a memory or in another document, fix it before
finishing the task — do not leave that work to the user.

## Errors and honesty

Same criterion as the rest of cerebro (see `SKILL.md`): an `error` is
reported as is with the suggested action; a connection error never authorizes
you to answer "from memory"; never invent the content of a document that does
not come from `docs_get`/`docs_search`/`docs_list`.
