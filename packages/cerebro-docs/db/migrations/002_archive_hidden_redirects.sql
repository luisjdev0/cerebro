-- cerebro-docs Phase 2 - real archiving, hidden/locked categories, and slug
-- redirects (luisjdev-pendientes/ecosistema-cerebro, work block from 2026-09-23).

-- Real archiving: replaces the hack of prefixing "[ARCHIVADO]" in the title. A
-- partial index is enough for the dedicated listing (GET /documents/archived) -- the
-- normal listing filters status='active' against the already-existing updated_at index.
ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived'));
CREATE INDEX documents_archived_idx ON documents (updated_at DESC) WHERE status = 'archived';

-- Hidden categories: `hidden` is toggleable (PATCH /categories/{slug}); `locked`
-- is only set when the category is created and can never be reverted (no endpoint
-- to touch it) -- for categories that must never be able to be revealed, like the
-- ones cerebro-flows will use for its reference .md files. The CHECK prevents a locked
-- category that isn't hidden (locking only makes sense to hide, never the other way around).
ALTER TABLE categories ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE categories ADD COLUMN locked BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE categories ADD CONSTRAINT categories_locked_requires_hidden CHECK (NOT locked OR hidden);

-- Slug redirects: a "symlink"-style record of old (category, slug) coordinates
-- -> current document, always pointing directly at the document_id (never chained to
-- another old slug). ON DELETE CASCADE: if the real document is deleted, its redirects
-- die with it -- there's no point resolving to a document that no longer exists.
CREATE TABLE slug_redirects (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    old_category TEXT NOT NULL,
    old_slug     TEXT NOT NULL,
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (old_category, old_slug)
);
