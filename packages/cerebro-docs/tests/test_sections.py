"""Unitarios del parseo de secciones por heading y de docs_patch_section
(cerebro_docs.sections). Sin I/O - mismo espiritu que tests/test_rrf.py y
tests/test_graph.py (parte unitaria) en cerebro-memory.
"""

from __future__ import annotations

import pytest

from cerebro_docs.sections import (
    VALID_OPERATIONS,
    AmbiguousHeadingError,
    HeadingNotFoundError,
    InvalidOperationError,
    apply_section_patch,
    find_headings,
    find_section,
)

SAMPLE = """# Titulo

## Introduccion
texto de introduccion.

## Detalles
### Sub A
contenido sub a.
### Sub B
contenido sub b.

## Cierre
texto final.
"""


# --------------------------------------------------------------------------- find_headings / find_section


def test_find_headings_detects_levels_1_to_6():
    text = "# uno\n## dos\n### tres\n#### cuatro\n##### cinco\n###### seis\n"
    levels = {h.title: h.level for h in find_headings(text)}
    assert levels == {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}


def test_section_ends_at_next_heading_of_same_or_higher_level():
    section = find_section(SAMPLE, "Detalles")
    lines = SAMPLE.splitlines()
    body = "\n".join(lines[section.start_line : section.end_line])
    assert "Sub A" in body
    assert "Sub B" in body
    assert "Cierre" not in body


def test_nested_heading_does_not_leak_past_its_own_level():
    section = find_section(SAMPLE, "Sub A")
    lines = SAMPLE.splitlines()
    body = "\n".join(lines[section.start_line : section.end_line])
    assert "contenido sub a." in body
    assert "Sub B" not in body


def test_last_section_extends_to_end_of_document():
    section = find_section(SAMPLE, "Cierre")
    assert section.end_line == len(SAMPLE.splitlines())


def test_find_section_raises_heading_not_found():
    with pytest.raises(HeadingNotFoundError):
        find_section(SAMPLE, "No existe")


def test_find_section_raises_ambiguous_on_duplicate_heading():
    dup = "## Repetido\na\n## Repetido\nb\n"
    with pytest.raises(AmbiguousHeadingError):
        find_section(dup, "Repetido")


# --------------------------------------------------------------------------- apply_section_patch: operations


def test_patch_replace_swaps_section_body_keeps_heading():
    result = apply_section_patch(SAMPLE, heading="Introduccion", operation="replace", body="nuevo contenido")
    assert "## Introduccion" in result
    assert "nuevo contenido" in result
    assert "texto de introduccion." not in result


def test_patch_append_adds_after_existing_body():
    result = apply_section_patch(SAMPLE, heading="Introduccion", operation="append", body="linea extra")
    assert "texto de introduccion." in result
    assert "linea extra" in result
    assert result.index("texto de introduccion.") < result.index("linea extra")
    # no se filtra a la siguiente seccion
    assert result.index("linea extra") < result.index("## Detalles")


def test_patch_delete_removes_whole_section_including_heading():
    result = apply_section_patch(SAMPLE, heading="Cierre", operation="delete")
    assert "## Cierre" not in result
    assert "texto final." not in result


def test_patch_insert_after_places_new_block_right_after_target_section():
    result = apply_section_patch(
        SAMPLE, heading="Introduccion", operation="insert_after", body="## Nueva\ncontenido nuevo"
    )
    assert "## Nueva" in result
    assert result.index("## Introduccion") < result.index("## Nueva") < result.index("## Detalles")


def test_patch_insert_before_places_new_block_right_before_target_section():
    result = apply_section_patch(SAMPLE, heading="Cierre", operation="insert_before", body="## Nueva\ncontenido")
    assert result.index("## Detalles") < result.index("## Nueva") < result.index("## Cierre")


# --------------------------------------------------------------------------- apply_section_patch: not found / ambiguous


def test_patch_raises_heading_not_found_without_create_if_missing():
    with pytest.raises(HeadingNotFoundError):
        apply_section_patch(SAMPLE, heading="No existe", operation="append", body="x")


def test_patch_ambiguous_heading_always_raises_even_with_create_if_missing():
    dup = "## Repetido\na\n## Repetido\nb\n"
    with pytest.raises(AmbiguousHeadingError):
        apply_section_patch(dup, heading="Repetido", operation="append", body="x", create_if_missing=True)


def test_patch_creates_missing_heading_when_flagged():
    result = apply_section_patch(
        SAMPLE, heading="Nueva seccion", operation="append", body="contenido nuevo", create_if_missing=True
    )
    assert "## Nueva seccion" in result
    assert "contenido nuevo" in result


def test_patch_create_if_missing_respects_new_heading_level():
    result = apply_section_patch(
        SAMPLE, heading="Nueva seccion", operation="replace", body="x", create_if_missing=True, new_heading_level=3
    )
    assert "### Nueva seccion" in result


def test_patch_delete_missing_heading_with_create_if_missing_is_a_noop():
    result = apply_section_patch(SAMPLE, heading="No existe", operation="delete", create_if_missing=True)
    assert result == SAMPLE


# --------------------------------------------------------------------------- vocabulary


def test_valid_operations_vocabulary():
    assert set(VALID_OPERATIONS) == {"replace", "append", "insert_after", "insert_before", "delete"}


def test_patch_rejects_operation_outside_vocabulary():
    with pytest.raises(InvalidOperationError):
        apply_section_patch(SAMPLE, heading="Introduccion", operation="frobnicate", body="x")  # type: ignore[arg-type]
