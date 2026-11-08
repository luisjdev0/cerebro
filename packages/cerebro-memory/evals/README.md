# Retrieval evaluation suite — KnowledgeOS

> **Note on language:** the documentation and code of this suite are in English
> like the rest of the repo, but the *datasets* (`memories.yaml`, `cases.yaml`)
> are deliberately in Spanish: they model a Spanish-speaking user, and the
> lexical collisions they test (`gastos`/`costos`, `migración`, `presupuesto`)
> only exist in that language. The YAML keys (`categoria`, `memorias_relevantes`,
> `memorias_trampa`, `contexto_esperado`) are part of the dataset schema and are
> kept as they are.

## Purpose

KnowledgeOS is a persistent memory system for AI agents. Before building or
adopting any retrieval engine (embeddings, knowledge graphs, hybrid search,
etc.) we need to be able to answer two questions objectively and repeatably:

1. **Does it find what it should find?** — when the user asks something, does
   the system retrieve the relevant memories in the top-k?
2. **Does it leak across contexts?** — the typical user has several life
   "worlds" (software projects, clients, personal finances, health,
   learning...). When they ask about one context, does the system return
   memories from another context that merely share vocabulary?

The canonical case that motivates this suite: the user has a software project
"Expense Tracker" and a personal domain "personal finances". Given the question
**"¿cuánto gasté este mes?"** ("how much did I spend this month?"), only
personal-finance memories should be retrieved — the software project's
memories (which talk about "gastos" and "costos" in the sense of a data table
or a hosting invoice) are **trap memories**: technically relevant by keyword,
but semantically from the wrong context.

This suite does not evaluate reasoning, answer writing or generation — only
**pure retrieval**: given a memory adapter and a query, which ids does it
return and in what order?

## Structure

```
evals/
├── README.md               this file
├── memories.yaml            synthetic corpus (~40 memories in 6 contexts)
├── cases.yaml                30 test cases against that corpus
├── harness/
│   ├── run_eval.py           harness runner (CLI)
│   ├── base.py                MemoryAdapter interface
│   ├── requirements.txt       (pyyaml only)
│   └── adapters/
│       ├── __init__.py        adapter registry (ADAPTERS)
│       ├── naive_keyword.py   reference adapter (baseline)
│       └── TEMPLATE.py        skeleton for new adapters (mem0/graphiti/letta/...)
└── results/
    └── <adapter>-<timestamp>.json   detail of each run (generated on execution)
```

## The corpus (`memories.yaml`)

~40 synthetic memories of a Spanish-speaking freelance developer, spread over
6 contexts:

| Context               | Kind       | Description                                    |
|------------------------|------------|------------------------------------------------|
| `expense-tracker`      | project    | finance app the user develops                   |
| `finanzas-personales`  | domain     | the user's real finances                        |
| `cliente-acme`         | project    | freelance project for the client "Acme"         |
| `infraestructura`      | domain     | their VPS, deployments, DNS, backups            |
| `salud`                | domain     | exercise, allergies, medical check-ups          |
| `aprendizaje`          | domain     | courses, readings, certifications               |

Each memory has `id`, `context`, `type` (semantic/episodic/procedural/decision),
`title`, `content` and `status` (`active` or `superseded`).

The corpus includes **deliberate lexical collisions** across contexts, to force
any keyword-based retrieval system (or one with poorly discriminative
embeddings) to get it wrong unless it isolates properly by context:

- **"gastos" / "costos"** (expenses / costs) — between `expense-tracker`
  (hosting costs, a `gastos` table in the data model) and `finanzas-personales`
  (the user's real expenses).
- **"migración" / "migrar"** (migration / to migrate) — between
  `infraestructura` (production OS migration, migrating services to Docker) and
  `cliente-acme` (the client's database migration).
- **"presupuesto"** (budget) — between `cliente-acme` (the project's approved
  budget) and `finanzas-personales` (personal monthly budget).

It also includes **3 "superseded → active" pairs**, facts that changed over
time:

- `inf-produccion-os-antiguo` (Ubuntu 22.04, superseded) →
  `inf-produccion-os-nuevo` (Ubuntu 24.04, active)
- `ca-tarifa-hora-antigua` ($35/h, superseded) → `ca-tarifa-hora-nueva`
  ($45/h, active)
- `fp-presupuesto-antiguo` (1,500/month, superseded) → `fp-presupuesto-mensual`
  (2,000/month, active)

By default the harness **only inserts `active` memories** — the `superseded`
ones exist to deliberately test whether a system returns an old fact when it
shouldn't anymore (use `--include-superseded`, see below).

## The cases (`cases.yaml`)

30 cases, each with: `id`, `categoria` (category), `query` (in Spanish,
natural), `memorias_relevantes` (ids that should come out in the top-k),
`memorias_trampa` (ids that should NOT come out, may be an empty list) and
`contexto_esperado` (expected context).

### 1. Direct (12 cases, prefix `d-`)

A single relevant memory, with no ambiguity in the relevant vocabulary.
`memorias_trampa` is empty (or nearly). They measure the base case: "does the
system find the obvious?".

Example: `d-004` — *"¿En qué proveedor está alojado mi VPS personal?"*
("Which provider hosts my personal VPS?") → `inf-vps-proveedor`.

### 2. Ambiguous (12 cases, prefix `amb-`)

The query shares vocabulary with memories from **two or more distinct
contexts**. `memorias_trampa` explicitly documents which memories, if they show
up in the top-k, count as contamination. This is the central category of the
suite — it measures whether the system tells contexts apart beyond lexical
surface.

The first ambiguous case is `amb-001`, the project's canonical case:

```yaml
- id: amb-001
  categoria: ambiguo
  query: "¿Cuánto gasté este mes?"
  memorias_relevantes: [fp-resumen-gastos-mes]
  memorias_trampa: [et-modelo-datos-gastos, et-costos-hosting]
  contexto_esperado: finanzas-personales
```

### 3. Temporal (6 cases, prefix `tmp-`)

The correct answer is the currently **active** memory; its `superseded` version
is the trap. They measure whether the system understands that a fact was
replaced, not just that "something related" exists. This category is only
meaningful if the corpus is indexed with `--include-superseded` (see below) —
otherwise the trap memory is not even in the index and contamination will be
trivially 0%.

## Metrics

For each case, with `k=5` (configurable with `--k`):

- **Precision@5** = (relevant memories in the top-5) / 5. Follows the standard
  IR convention: if the adapter returns fewer than 5 results, the empty
  positions count as "not relevant" (the denominator is not reduced).
- **Recall@5** = (relevant memories in the top-5) / (total relevant memories
  for the case). In this suite almost every case has a single relevant memory,
  so recall@5 is binary (0% or 100%) per case.
- **Contamination rate** = fraction of cases, within a category, where **at
  least one** trap memory shows up in the top-5. It is the most important
  metric of this project: it measures context leaks, not just whether
  "something relevant was found".

The runner aggregates these three metrics per category (direct/ambiguous/
temporal) and overall.

## How to run the harness

```bash
# install the only dependency
python -m pip install -r evals/harness/requirements.txt

# run with the reference adapter (naive keyword baseline)
python evals/harness/run_eval.py --adapter naive

# include the superseded memories (so the "temporal" category is meaningful)
python evals/harness/run_eval.py --adapter naive --include-superseded

# different top-k, don't save the JSON, point to another corpus, etc.
python evals/harness/run_eval.py --adapter naive --k 10 --no-save
python evals/harness/run_eval.py --adapter naive --memories other_corpus.yaml --cases other_cases.yaml
```

Each run prints a table with precision@k, recall@k and contamination rate per
category, and (unless you use `--no-save`) saves the full per-case detail in
`evals/results/<adapter>-<timestamp>.json`.

### What to expect from the `naive` adapter

`NaiveKeywordAdapter` (in `evals/harness/adapters/naive_keyword.py`) ranks
memories by overlap of normalized tokens (minus basic Spanish stopwords), with
no concept of context or time. It is a deliberately poor baseline, to verify
that the whole harness runs without installing anything heavier, and to have a
"reasonable worst case" comparison point.

It is expected (and correct/desirable) to show:

- Reasonable precision@5 and recall@5 on `directo` (it finds the obvious).
- **High contamination on `ambiguo`** — by design it does not tell contexts
  apart, so the corpus's lexical collisions (gastos/costos, migración,
  presupuesto) fool it.
- **High contamination on `temporal` when run with `--include-superseded`** —
  it has no notion of "current vs. replaced", so old memories compete on equal
  footing with new ones.

Any real adapter (mem0, graphiti, letta, or KnowledgeOS's own engine) should
clearly beat the naive one, especially on contamination rate for `ambiguo` and
`temporal` — that is the signal that the system really isolates context and
understands temporal validity, not just that it "searches well".

## How to add your own cases

1. If you need a new memory, add it to `evals/memories.yaml` with a unique `id`
   (slug, prefixed by context), `context`, `type`, `title`, `content` and
   `status`.
2. Add the case to `evals/cases.yaml`:
   - `id`: category prefix (`d-`, `amb-`, `tmp-`) followed by a number.
   - `categoria`: `directo`, `ambiguo` or `temporal`.
   - `query`: in Spanish, the way a real user would write it.
   - `memorias_relevantes`: ids that should come out in the top-k.
   - `memorias_trampa`: ids from the wrong context (or the superseded version,
     in temporal cases) that should NOT come out. Empty in direct cases with no
     real collision risk.
   - `contexto_esperado`: the `context` the correct answer belongs to.
3. Run the harness (`python evals/harness/run_eval.py --adapter naive`) — the
   runner automatically validates that every id referenced in `cases.yaml`
   exists in `memories.yaml` and warns (without aborting) if one is missing.
4. If you are testing new lexical collisions, check that the "trap" memory and
   the "relevant" memory share real vocabulary (not just the same thematic
   context) — that is the signal that makes the case useful as ambiguous.

## How to add a new adapter (mem0, graphiti, letta, real KnowledgeOS, ...)

1. Copy `evals/harness/adapters/TEMPLATE.py` to
   `evals/harness/adapters/<system_name>.py` and rename the class.
2. Implement `setup()`, `insert(memory)`, `search(query, k)` and `teardown()`
   according to the `MemoryAdapter` interface (`evals/harness/base.py`). The
   key contract: `search()` must return the corpus `id`s (not the system's
   internal ids, not objects), in descending relevance order.
3. Register it in `evals/harness/adapters/__init__.py` (`ADAPTERS` dict).
4. Run `python evals/harness/run_eval.py --adapter <system_name>`.

`TEMPLATE.py` includes TODOs and docstrings with concrete examples of what to
call in mem0, graphiti and letta — it is just the skeleton, not a real
implementation.
