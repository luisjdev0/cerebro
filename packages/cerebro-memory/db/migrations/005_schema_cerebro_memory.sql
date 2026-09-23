-- cerebro-memory - Migration to its own `cerebro_memory` schema (leaves `public` free
-- to coexist with a future sibling schema `cerebro_docs` in the same Postgres instance).
--
-- Moves ALL of the service's tables (including schema_migrations, the runner's own
-- control table) from `public` to `cerebro_memory` via ALTER TABLE ... SET SCHEMA.
-- Indexes and sequences travel along with their table; there are no functions/triggers
-- of the service to move (only DEFAULTs like gen_random_uuid()/now(), resolved via the
-- extension, not via schema-qualified functions).
--
-- The `vector` (pgvector) and `pgcrypto` extensions stay in `public` on purpose: they
-- are extensions shared by the instance, not tables of this service, and the
-- `vector` type/its operators keep resolving because `public` remains in the
-- pool's search_path (see db.py: search_path = "cerebro_memory, public").
--
-- Idempotency on a fresh install: on a new DB, 001-004 run first (creating
-- everything in `public`, because `cerebro_memory` does not exist yet at that point --
-- the pool's search_path falls back to `public` when it can't find the schema) and
-- this migration (005) then moves everything to `cerebro_memory`, within the same
-- startup run.

CREATE SCHEMA IF NOT EXISTS cerebro_memory;

-- Phase 1 (001_init.sql)
ALTER TABLE public.contexts SET SCHEMA cerebro_memory;
ALTER TABLE public.memories SET SCHEMA cerebro_memory;
ALTER TABLE public.audit_log SET SCHEMA cerebro_memory;

-- Phase 2 (002_context_engine.sql)
ALTER TABLE public.disambiguation_log SET SCHEMA cerebro_memory;
ALTER TABLE public.context_preferences SET SCHEMA cerebro_memory;

-- Phase 3 (003_edges.sql)
ALTER TABLE public.memory_edges SET SCHEMA cerebro_memory;

-- v1.0 (004_api_tokens.sql)
ALTER TABLE public.api_tokens SET SCHEMA cerebro_memory;

-- Migration runner control table (created by db.py, not by a migration
-- file) -- also moved so the entire service lives in a single schema.
ALTER TABLE public.schema_migrations SET SCHEMA cerebro_memory;
