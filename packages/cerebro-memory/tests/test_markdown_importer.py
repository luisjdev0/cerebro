"""Unit tests for the Markdown parser (Phase 5, connector 1, plan_v2.md SS8).

All of `cerebro_memory.markdown_importer` is pure parsing (it only reads files from
`tests/fixtures/`, it does not touch the API or Postgres), so it runs without a
database - same spirit as tests/test_rrf.py and tests/test_context_engine.py.
"""

from __future__ import annotations

from pathlib import Path

from cerebro_memory.markdown_importer import (
    iter_markdown_files,
    is_memory_index,
    parse_frontmatter_file,
    parse_markdown_file,
    truncate_large_code_blocks,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- frontmatter


def test_frontmatter_user_type_maps_to_semantic_no_importance_override():
    memories = parse_markdown_file(FIXTURES / "frontmatter_user.md")
    assert len(memories) == 1
    mem = memories[0]
    assert mem.title == "Prefiere usar Neovim para todo"
    assert mem.type == "semantic"
    assert mem.importance is None
    assert "Neovim" in mem.content
    # the frontmatter must not leak into the content
    assert "metadata" not in mem.content
    assert "---" not in mem.content


def test_frontmatter_project_type_maps_to_semantic_with_importance_07():
    memories = parse_markdown_file(FIXTURES / "frontmatter_project.md")
    assert len(memories) == 1
    mem = memories[0]
    assert mem.title == "Arquitectura del proyecto Cerebro"
    assert mem.type == "semantic"
    assert mem.importance == 0.7


def test_frontmatter_without_name_key_is_not_a_claude_code_memory():
    text = (FIXTURES / "frontmatter_no_name.md").read_text(encoding="utf-8")
    assert parse_frontmatter_file(FIXTURES / "frontmatter_no_name.md", text) is None


def test_frontmatter_without_name_falls_back_to_generic_parsing():
    # full dispatch (parse_markdown_file): since it's not valid frontmatter, it must
    # fall through to the generic parser and still produce the section with the real heading.
    memories = parse_markdown_file(FIXTURES / "frontmatter_no_name.md")
    titles = [m.title for m in memories]
    assert "Solo un heading" in titles


# --------------------------------------------------------------------------- MEMORY.md index


def test_is_memory_index_detects_bullet_link_format():
    path = FIXTURES / "memory_index" / "MEMORY.md"
    text = path.read_text(encoding="utf-8")
    assert is_memory_index(path, text) is True


def test_is_memory_index_false_for_non_memory_filename():
    path = FIXTURES / "generic_headings.md"
    text = path.read_text(encoding="utf-8")
    assert is_memory_index(path, text) is False


def test_memory_index_follows_existing_link_and_falls_back_for_missing_one():
    memories = parse_markdown_file(FIXTURES / "memory_index" / "MEMORY.md")
    titles = [m.title for m in memories]

    # The existing link (linked_pref.md) is followed and parsed (generic, no
    # headings -> a single memory named after the file).
    assert "linked_pref" in titles
    followed = next(m for m in memories if m.title == "linked_pref")
    assert "Neovim" in followed.content

    # The broken link (no-existe.md) degrades to the bullet itself as a small memory.
    assert "Nota suelta sin archivo" in titles
    fallback = next(m for m in memories if m.title == "Nota suelta sin archivo")
    assert fallback.content == "Le gusta el cafe negro"


# --------------------------------------------------------------------------- generic headings


def test_generic_markdown_splits_by_headings_and_merges_small_sections():
    memories = parse_markdown_file(FIXTURES / "generic_headings.md")
    titles = [m.title for m in memories]

    # "Nota corta" (1 real line) must merge into the previous section
    # ("Preferencias"), not appear as its own memory.
    assert "Nota corta" not in titles
    assert "Preferencias" in titles
    prefs = next(m for m in memories if m.title == "Preferencias")
    assert "cafe" in prefs.content
    assert "Solo una linea." in prefs.content  # the merged content is still present

    assert "Codigo de ejemplo" in titles


def test_generic_markdown_truncates_large_code_blocks():
    memories = parse_markdown_file(FIXTURES / "generic_headings.md")
    code_section = next(m for m in memories if m.title == "Codigo de ejemplo")
    assert "[código truncado]" in code_section.content
    assert "line_0" not in code_section.content
    assert "Texto despues del bloque de codigo" in code_section.content


def test_truncate_large_code_blocks_keeps_small_blocks_untouched():
    text = "```python\nx = 1\ny = 2\n```\n"
    assert truncate_large_code_blocks(text) == text


# --------------------------------------------------------------------------- iter_markdown_files


def test_iter_markdown_files_single_file_returns_itself():
    f = FIXTURES / "frontmatter_user.md"
    assert iter_markdown_files(f) == [f]


def test_iter_markdown_files_directory_finds_all_md_recursively():
    files = iter_markdown_files(FIXTURES)
    assert (FIXTURES / "memory_index" / "MEMORY.md") in files
    assert (FIXTURES / "memory_index" / "linked_pref.md") in files
    assert (FIXTURES / "generic_headings.md") in files
