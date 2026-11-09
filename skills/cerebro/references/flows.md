# cerebro-flows (`flow_*`)

A third kind of content, besides memory (distilled facts) and documents
(complete Markdown): processes with steps, decisions and approval
checkpoints, defined in YAML. `cerebro-flows` is a **traffic light**, not an
orchestrator: it never runs third-party tools on its own (Jira, SSH,
whatever) — you remain the one who performs the real actions with your own
tools. Its only job is to decide which step you get to see and when, and never
to hand you the full definition at once.

This reference covers two separate things: **running** an existing flow (most
of the time) and **creating/editing** a new definition.

## Running a flow (step-by-step protocol)

**Never read a full definition (`flow_get`) in order to "follow" it by hand.**
The server reveals one step at a time on purpose — that way "you shouldn't
skip a checkpoint" becomes "you cannot see the next step until you approved
it", a design problem, not a matter of your discipline.

1. **`flow_start(code)`** → `{run_id, status, step}`. Keep the `run_id`: you
   need it in ALL the following calls (it is an opaque token, there is no
   implicit session).
2. The first `step` is almost always synthetic
   (`id: "__prerequisites__"`, `tools_required: [...]`): confirm that you have
   those tools available (try `ToolSearch` if one is deferred in your
   environment) before going on. If one is truly missing, tell the user and do
   NOT continue — there is no way to "skip" this step.
3. Call **`flow_next(run_id)`** in a loop to advance. If the `step` it returns
   is `type: "decision"`, your NEXT call to `flow_next` must include
   `decision` with one of the keys of `branches` — the server does not infer
   the condition, you report it explicitly based on what you observed/decided.
4. If a `step` carries a `checkpoint`, `flow_next` BLOCKS (409) until you call
   **`flow_approve_checkpoint(run_id)`** or
   **`flow_reject_checkpoint(run_id, reason)`**:
   - Approving is the highest-consequence action in a flow — if you are unsure
     whether the user would approve this particular step, confirm with them
     BEFORE calling `flow_approve_checkpoint`, just as you would with any
     irreversible action.
   - Rejecting does not stop the flow: it redirects it to the definition's
     `checkpoint.on_reject` step (e.g. back to review an earlier step).
5. `status: "completed"` (with `step: null`) marks the successful end. There is
   no need to recognize any free text like "end of flow" — it is a structured
   field.
6. If the user explicitly decides not to continue with a process that has
   already started, use **`flow_abort(run_id, reason?)`** — it is different
   from a rejected checkpoint (which reroutes, it does not end) and it is
   irreversible.

A `run_id` abandoned without `flow_abort` expires on its own through
inactivity (the server handles it with a TTL) — you do not need to "clean it
up", but prefer to abort explicitly when you know the process will not
continue, so that it is reflected in the history.

## Creating or editing a flow

The authoring tools are first-class — there is no need to go through the CLI
to define a new process:

1. **Pick or create a category** with `flow_categories()` /
   `flow_create_category(slug, code, name, description?)`. The category's
   `code` is the prefix of its flows' ids (category `incident`/`INC` → flows
   `INC-1`, `INC-2`...).
2. **Write the YAML** (see the full schema below).
3. **Validate before saving** with `flow_validate(yaml_content)` — it runs
   exactly the same validation that `flow_save`/`flow_update` would, but
   without touching the database. It returns the specific error (which step,
   which field) if something is wrong; iterate on that before committing
   anything.
4. **Save** with `flow_save(category, yaml_content, code?)` — the `code` is
   auto-generated if you omit it. To modify an existing flow use
   `flow_update(code, yaml_content)` (replaces the whole YAML, versioned: a run
   already in progress keeps following the version it started with).
5. `flow_get(code)`/`flow_list(category?)` to inspect what already exists before
   creating something redundant — same criterion as with memory and
   documents: search before duplicating.
5b. **Showing a flow to the person: `flow_diagram(code, version?)`.** It returns
   `diagram` (Mermaid flowchart source), `code`, `version` and `format`, generated from
   the complete definition: tasks as boxes, decisions as diamonds with their labeled
   branches, delegates (parallel groups together), terminals, and approval checkpoints with
   their rejection path. Present the `diagram` in a ```mermaid code block so it renders.
   Use it to review, explain or document a flow — NEVER to execute one: running a flow is
   `flow_start`/`flow_next`, one step at a time. Omit `version` for the current one; a
   wrong code or version returns `error`.
6. `flow_delete(code)` is irreversible (it also deletes the run history) —
   confirm with the user if in doubt.

## YAML format

```yaml
metadata:
  name: Incident handling
  category: incident          # informational; the real category is decided by
                               # the `category` parameter of flow_save
  description: Analyze and resolve incidents reported by users.
  author: jose

entry: analyze                 # single entry point -- "skipping steps" is
                                # modeled as a decision inside the procedure,
                                # not as multiple entry_points

procedure:
  - id: analyze
    type: task                 # task | decision | delegate
    description: Analyze the incident.
    next: investigate

  - id: investigate
    type: task
    description: Investigate possible causes.
    next: decision

  - id: decision
    type: decision
    description: Is there enough information?
    branches:                  # a decision NEVER carries `next`, only branches
      sufficient: resolve
      insufficient: investigate

  - id: resolve
    type: task
    description: Resolve the incident.
    checkpoint:                 # composable property on any step, not a
                                 # separate type
      required: true
      prompt: "Do you confirm the resolution ticket should be created?"
      on_reject: investigate
    tools_allowed: [create_ticket]
    next: end

  - id: end
    type: task
    description: Close the flow.
    terminal: true              # explicit marker -- the end is never inferred
                                 # from the absence of next

rules:
  - id: R001
    rule: Do not assume information that is not present in the context.
    applies_to: [investigate, decision]   # empty/omitted = applies to everything

tools:
  - search_incidents
  - create_ticket

execution_policy:
  autonomy: guided
  confirmation_required: [create_ticket]
```

Validity rules (what `flow_validate` checks, with the exact step/field in the
message if it fails):

- `entry` must exist in `procedure`, and every step `id` must be unique.
- Every `next`, `branches.<condition>` and `checkpoint.on_reject` must point to
  a real `id` in the `procedure` — no dangling references.
- A `decision` step **must** have `branches` (non-empty) and **cannot** have
  `next`. A non-terminal `task`/`delegate` step **must** have `next`.
- A `terminal: true` step cannot have `next` or `branches`, and the
  `procedure` needs at least one terminal step (otherwise the flow never
  closes).
- `type: delegate` requires the `delegate: {agent_type, prompt_ref?,
  parallel_group?}` block; that block does not apply to `task`/`decision`. Two
  or more `delegate` steps sharing a `parallel_group` must all point to the
  same `next` (they are revealed together in a single `flow_next` response, to
  represent concurrent work — e.g. an auditor and a grader working in
  parallel).
- `rules[].applies_to`, if used, must reference steps that exist.

A YAML detail worth knowing: `cerebro-flows` normalizes the parser so that
`yes`/`no` (the branches of a yes/no decision, the most natural case in the
world) are stored as the literal strings "yes"/"no", NOT as booleans — you can
use them confidently in `branches` without quotes. `true`/`false` are
interpreted as real booleans (you use them in `terminal` and
`checkpoint.required`).

## Errors and honesty

Same criterion as the rest of cerebro (see `SKILL.md`): an `error` is reported
as is; never invent which step a flow is on if it does not come from
`flow_next`/`flow_start`/the real state of a `run_id`.
