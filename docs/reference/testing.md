# Tests

> How each package's suite runs. Moved out of the README; see the [documentation index](../README.md).


```bash
# each package has its own suite (testpaths = ["tests"] in its pyproject.toml)
cd packages/cerebro-memory && pytest
cd packages/cerebro-docs && pytest
cd packages/cerebro-flows && pytest
cd packages/cerebro-clients && pytest
cd packages/cerebro-mcp && pytest
cd packages/cerebro-cli && pytest
```

In `cerebro-memory`: `tests/test_rrf.py`, `tests/test_security.py`,
`tests/test_context_engine.py`, the unit part of `tests/test_auth.py`
(`Principal`, `hash_token`/`generate_token`), and the unit part of
`tests/test_graph.py` (relationship vocabulary) are unit tests (no database).
`tests/test_supersedence.py`, the integration part of `tests/test_auth.py` (token
lifecycle, scope enforcement and `allowed_contexts` enforcement, `DELETE
/contexts/{slug}`), and the integration part of `tests/test_graph.py` (edges,
no-duplicates, direction in `related`, hard-delete cascade, `timeline`
ordering) are skipped automatically if `DATABASE_URL` isn't reachable (run
`docker compose up -d` first from the repo root).

In `cerebro-docs`: `tests/test_auth.py`, `tests/test_documents.py`,
`tests/test_sections.py`, `tests/test_slug_redirects.py`, `tests/test_slugs.py`,
`tests/test_strict_input.py` -- same criterion, the integration part needs
`DATABASE_URL` reachable.

In `cerebro-flows`: `tests/test_schema.py` is a unit test (YAML parsing/referential
validation, no DB); `tests/test_auth.py` and `tests/test_flows.py` (CRUD + the full
execution engine, including an end-to-end run through the example flow `INC-22` from the
design document) also need `REDIS_URL` reachable, not just Postgres.

Packages with an integration suite (`cerebro-memory`, `cerebro-docs`,
`cerebro-flows`, `cerebro-cli`, and transitively `cerebro-clients`) isolate their tests
against an ephemeral `cerebro_test` database (dropped/recreated in each package's
`pytest_configure`, before any test module is imported) -- they never write against
the real development database. See `packages/cerebro-memory/tests/conftest.py` for the
mechanism's detail, and `packages/cerebro-flows/tests/conftest.py` for its variant
(it also runs `FLUSHDB` on a dedicated Redis database, `/15`).
