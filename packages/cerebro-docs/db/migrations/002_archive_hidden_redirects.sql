-- cerebro-docs Fase 2 - archivado real, categorias ocultas/bloqueadas y redirects de
-- slug (luisjdev-pendientes/ecosistema-cerebro, bloque de trabajo del 2026-09-23).

-- Archivado real: reemplaza el hack de prefijar "[ARCHIVADO]" en el titulo. Un indice
-- parcial basta para la enumeracion dedicada (GET /documents/archived) -- el listado
-- normal filtra status='active' contra el indice ya existente de updated_at.
ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived'));
CREATE INDEX documents_archived_idx ON documents (updated_at DESC) WHERE status = 'archived';

-- Categorias ocultas: `hidden` es alternable (PATCH /categories/{slug}); `locked`
-- solo se fija al crear la categoria y nunca se puede revertir (sin endpoint para
-- tocarlo) -- para categorias que nunca deben poder revelarse, como las que usara
-- cerebro-flows para sus .md de referencia. El CHECK impide una categoria locked que
-- no sea hidden (bloquear solo tiene sentido para ocultar, nunca al reves).
ALTER TABLE categories ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE categories ADD COLUMN locked BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE categories ADD CONSTRAINT categories_locked_requires_hidden CHECK (NOT locked OR hidden);

-- Redirects de slug: registro tipo "symlink" de coordenadas (categoria, slug) viejas
-- -> documento actual, siempre apuntando directo al document_id (nunca encadenado a
-- otro slug viejo). ON DELETE CASCADE: si el documento real se borra, sus redirects
-- mueren con el -- no tiene sentido resolver a un documento que ya no existe.
CREATE TABLE slug_redirects (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    old_category TEXT NOT NULL,
    old_slug     TEXT NOT NULL,
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (old_category, old_slug)
);
