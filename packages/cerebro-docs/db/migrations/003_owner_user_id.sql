-- cerebro-docs Phase 3 -- ownership (ecosistema-cerebro.md SS13, auth unification):
-- which human (cerebro_auth.users) owns each document, distinct from `created_by`
-- (the agent/channel identifier, untouched by this migration).
--
-- No FK across schemas: `cerebro_auth` is owned by the sibling `cerebro-auth`
-- service/package, and this project's convention is never to take a hard
-- cross-schema dependency (SS8/SS14) -- a query is fine, a constraint that could
-- fail a write because of another schema's row is not. Enforcement of the
-- relationship lives at the application layer instead (see `auth.py`'s
-- `owner_filter` and its use in `api.py`).
ALTER TABLE documents ADD COLUMN owner_user_id UUID;

COMMENT ON COLUMN documents.owner_user_id IS
    'References cerebro_auth.users.id (no FK: cross-schema, owned by packages/cerebro-auth). NULL for documents created by a service/root token with no user_id.';
