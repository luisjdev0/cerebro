"""Unit tests for slug generation/normalization/collision (cerebro_docs.slugs).

No I/O - same spirit as tests/test_rrf.py in cerebro-memory.
"""

from __future__ import annotations

from cerebro_docs.slugs import FALLBACK_SLUG, slugify


def test_slugify_lowercases():
    assert slugify("Plan De Ecosistema") == "plan-de-ecosistema"


def test_slugify_strips_accents():
    assert slugify("Categoría Ecosistema Cerebro") == "categoria-ecosistema-cerebro"


def test_slugify_replaces_spaces_with_dashes():
    assert slugify("hola   mundo") == "hola-mundo"


def test_slugify_strips_invalid_chars():
    assert slugify("Título: guía v1.0!") == "titulo-guia-v1-0"


def test_slugify_collapses_repeated_dashes():
    assert slugify("a -- b") == "a-b"


def test_slugify_strips_leading_and_trailing_dashes():
    assert slugify("---hola---") == "hola"


def test_slugify_is_deterministic_same_title_same_slug():
    # Two documents with the same title in the same category must COLLIDE in the
    # database (UNIQUE (category_id, slug), see api.py) - that only works if
    # slugify() is deterministic for the same input.
    assert slugify("Plan de arquitectura") == slugify("Plan de arquitectura")


def test_slugify_different_titles_produce_different_slugs():
    assert slugify("Plan de arquitectura") != slugify("Plan de despliegue")


def test_slugify_falls_back_when_nothing_valid_remains():
    assert slugify("!!!") == FALLBACK_SLUG
    assert slugify("") == FALLBACK_SLUG
    assert slugify("   ") == FALLBACK_SLUG
