-- KnowledgeOS - Migracion al schema propio `cerebro_memory` (deja `public` libre para
-- convivir con un futuro schema hermano `cerebro_docs` en la misma instancia Postgres).
--
-- Mueve TODAS las tablas del servicio (incluida schema_migrations, la tabla de control
-- del propio runner) de `public` a `cerebro_memory` via ALTER TABLE ... SET SCHEMA.
-- Indices y secuencias viajan solos con su tabla; no hay funciones/triggers propios del
-- servicio que mover (solo DEFAULTs como gen_random_uuid()/now(), resueltos via la
-- extension, no via schema-qualified functions).
--
-- La extension `vector` (pgvector) y `pgcrypto` se quedan en `public` a proposito: son
-- extensiones compartidas de la instancia, no tablas de este servicio, y el tipo
-- `vector`/sus operadores siguen siendo resueltos porque `public` permanece en el
-- search_path del pool (ver db.py: search_path = "cerebro_memory, public").
--
-- Idempotencia en instalacion fresca: en una DB nueva, 001-004 corren primero (crean
-- todo en `public`, porque `cerebro_memory` todavia no existe en ese momento -- el
-- search_path del pool cae a `public` al no encontrar el schema) y esta migracion
-- (005) mueve todo a `cerebro_memory` a continuacion, dentro del mismo arranque.

CREATE SCHEMA IF NOT EXISTS cerebro_memory;

-- Fase 1 (001_init.sql)
ALTER TABLE public.contexts SET SCHEMA cerebro_memory;
ALTER TABLE public.memories SET SCHEMA cerebro_memory;
ALTER TABLE public.audit_log SET SCHEMA cerebro_memory;

-- Fase 2 (002_context_engine.sql)
ALTER TABLE public.disambiguation_log SET SCHEMA cerebro_memory;
ALTER TABLE public.context_preferences SET SCHEMA cerebro_memory;

-- Fase 3 (003_edges.sql)
ALTER TABLE public.memory_edges SET SCHEMA cerebro_memory;

-- v1.0 (004_api_tokens.sql)
ALTER TABLE public.api_tokens SET SCHEMA cerebro_memory;

-- Tabla de control del migration runner (creada por db.py, no por un archivo de
-- migracion) -- tambien se mueve para que quede todo el servicio en un solo schema.
ALTER TABLE public.schema_migrations SET SCHEMA cerebro_memory;
