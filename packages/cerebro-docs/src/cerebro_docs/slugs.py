"""Slug generation and normalization for documents (ecosistema-cerebro.md SS6).

Deliberately pure (no I/O), so it's testable without a database - real COLLISION
detection lives in the database's `UNIQUE (category_id, slug)` constraint
(see api.py, which translates Postgres's `UniqueViolationError` into an explicit
409): two documents with the same title in the same category deliberately
generate the same slug here - it's the database, not this module, that decides
whether that's a real conflict.
"""

from __future__ import annotations

import re
import unicodedata

FALLBACK_SLUG = "documento"

_WHITESPACE_RE = re.compile(r"\s+")
_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_REPEATED_DASH_RE = re.compile(r"-{2,}")


def slugify(title: str) -> str:
    """lowercase, no accents, spaces -> dashes, only [a-z0-9-].

    Repeated dashes are collapsed and leading/trailing ones are trimmed. A title that
    leaves no valid character (e.g. only emoji or punctuation) falls back to
    `FALLBACK_SLUG` instead of producing an empty slug.
    """
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.strip().lower()
    dashed = _WHITESPACE_RE.sub("-", lowered)
    cleaned = _INVALID_CHARS_RE.sub("-", dashed)
    collapsed = _REPEATED_DASH_RE.sub("-", cleaned).strip("-")
    return collapsed or FALLBACK_SLUG
