-- Ownership (unified-auth migration, packages/cerebro-auth): which cerebro_auth.users
-- row is the human owner of a memory, for the new owner-based access filter (see
-- src/cerebro_memory/auth.py Principal.owner_filter and the "Change 3" description
-- in the unify-auth plan). Nullable: NULL means "no specific human owner" -- created
-- by the root token or a service/root-adjacent cerebro_auth.api_tokens row with no
-- user_id, or a memory that predates this column.
--
-- Distinct from the pre-existing `source` column (an agent/channel identifier) --
-- owner_user_id is a new, separate concept (the human owner), not a replacement.
--
-- No FK: `cerebro_auth` lives in a separate schema, owned by the sibling
-- packages/cerebro-auth package, and this migration intentionally avoids a
-- cross-schema foreign key (project convention: migrations never reach into a
-- schema they don't own). owner_user_id conceptually references
-- cerebro_auth.users(id).

ALTER TABLE memories ADD COLUMN owner_user_id UUID;

CREATE INDEX memories_owner_user_id_idx ON memories (owner_user_id);
