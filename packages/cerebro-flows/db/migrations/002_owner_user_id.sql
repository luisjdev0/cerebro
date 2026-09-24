-- cerebro-flows: ownership column on flow_definitions (unifying auth into the
-- shared `cerebro_auth` schema, owned by the `cerebro-auth` package).
--
-- No FK across schemas (this project's convention: sibling modules never take a
-- hard cross-schema dependency) -- `owner_user_id` conceptually references
-- `cerebro_auth.users.id`, enforced only in application code (`auth.py`'s
-- `owner_filter` resolution + `api.py`'s `require_owner_allowed`).
--
-- Only `flow_definitions` gets this column: ownership is about who authored a
-- flow definition, not who is currently executing a run of it (`flow_runs` /
-- `flow_run_events` / `flow_definition_versions` are untouched -- run-level
-- ownership is a separate, not-yet-designed concern).

ALTER TABLE flow_definitions ADD COLUMN owner_user_id UUID;
