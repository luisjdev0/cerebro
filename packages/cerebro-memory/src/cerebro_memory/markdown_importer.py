"""Pure parsing for the Markdown importer (Phase 5, connector 1, plan_v2.md SS8).

Deliberately Phase 5's first connector: importing existing `MEMORY.md`/`CLAUDE.md`/
loose notes is what solves migration from the user's status quo (the same argument
plan_v2.md SS8/SS11 uses to pick it as connector #1).

Everything in this module is pure parsing (it only reads files from the filesystem, it
does not talk to the API or to Postgres) so that it is testable with nothing more than
fixtures on disk - see tests/test_markdown_importer.py. Orchestration (dedup via
search, POST /memories, handling 422s for credentials, final report) lives in
`cerebro_memory.cli`.

Three recognized formats, in this order of preference (`parse_markdown_file`):

    1. Claude Code memory-style YAML frontmatter (`name`, `description`,
       `metadata.type`) -> a single memory per file.
    2. `MEMORY.md` index (lines `- [title](file.md) — hook`) -> follows the links
       if the files exist (recursively, via parse_markdown_file); if not,
       each bullet becomes its own small memory.
    3. Generic Markdown -> split by level 1-2 headings; each section with >= 2
       lines of real content is a memory, smaller ones are merged with the
       previous one.

In all three cases, code blocks longer than 30 lines are truncated to
"[código truncado]" before any other processing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CODE_BLOCK_MAX_LINES = 30
CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*\r?\n?(.*)\Z", re.DOTALL)

# `- [title](file.md) — hook` (the hook's em/en dash is optional).
INDEX_LINE_RE = re.compile(
    r"^-\s*\[(?P<title>[^\]]+)\]\((?P<link>[^)]+)\)(?:\s*[—–-]\s*(?P<hook>.*))?\s*$"
)

HEADING_RE = re.compile(r"^(#{1,2})\s+(.*?)\s*$")

# metadata.type (memoria Claude Code) -> (memory type, importance override)
_FRONTMATTER_TYPE_MAP: dict[str, tuple[str, float | None]] = {
    "user": ("semantic", None),
    "feedback": ("semantic", None),
    "reference": ("semantic", None),
    "project": ("semantic", 0.7),
}


@dataclass
class ParsedMemory:
    title: str
    content: str
    type: str = "semantic"
    importance: float | None = None
    source_path: str | None = None


# --------------------------------------------------------------------------- utils


def truncate_large_code_blocks(text: str, max_lines: int = CODE_BLOCK_MAX_LINES) -> str:
    """Replaces the inner content of any ``` ``` block longer than
    `max_lines` lines with "[código truncado]", keeping the fences."""

    def _replace(m: re.Match[str]) -> str:
        block = m.group(0)
        inner = m.group(1)
        inner_lines = inner.splitlines()
        if len(inner_lines) <= max_lines:
            return block
        fence_line = block.splitlines()[0]  # e.g. "```python"
        return f"{fence_line}\n[código truncado]\n```"

    return CODE_BLOCK_RE.sub(_replace, text)


# --------------------------------------------------------------------------- 1. frontmatter


def parse_frontmatter_file(path: Path, text: str) -> list[ParsedMemory] | None:
    """Claude Code memory-style YAML frontmatter. Returns None (not applicable, the
    caller should try the next format) if there is no parseable frontmatter or if it
    does not have the expected shape (missing `name`)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None

    fm_text, body = m.group(1), m.group(2)
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or "name" not in data:
        return None

    title = str(data.get("description") or data.get("name") or path.stem)
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    type_raw = str((meta or {}).get("type", "user")).strip().lower()
    mem_type, importance = _FRONTMATTER_TYPE_MAP.get(type_raw, ("semantic", None))

    content = truncate_large_code_blocks(body).strip()
    if not content:
        return None

    return [
        ParsedMemory(
            title=title,
            content=content,
            type=mem_type,
            importance=importance,
            source_path=str(path),
        )
    ]


# --------------------------------------------------------------------------- 2. MEMORY.md index


def is_memory_index(path: Path, text: str) -> bool:
    """Heuristic: named MEMORY.md (case-insensitive) AND contains at least
    one `- [title](file)` line."""
    if path.name.lower() != "memory.md":
        return False
    return any(INDEX_LINE_RE.match(line.strip()) for line in text.splitlines())


def parse_memory_index(path: Path, text: str) -> list[ParsedMemory]:
    base_dir = path.parent
    memories: list[ParsedMemory] = []

    for line in text.splitlines():
        m = INDEX_LINE_RE.match(line.strip())
        if not m:
            continue
        title = m.group("title").strip()
        link = m.group("link").strip()
        hook = (m.group("hook") or "").strip()

        linked_path = (base_dir / link).resolve()
        if linked_path.is_file():
            try:
                sub_memories = parse_markdown_file(linked_path)
            except (OSError, UnicodeDecodeError):
                sub_memories = []
            if sub_memories:
                memories.extend(sub_memories)
                continue

        # The file doesn't exist (or came out empty after parsing): the bullet itself
        # becomes a small memory.
        content = hook or title
        memories.append(
            ParsedMemory(title=title, content=content, type="semantic", source_path=str(path))
        )

    return memories


# --------------------------------------------------------------------------- 3. generic


def parse_generic_markdown(path: Path, text: str) -> list[ParsedMemory]:
    text = truncate_large_code_blocks(text)
    lines = text.splitlines()

    sections: list[tuple[str, list[str]]] = []
    current_title = path.stem
    current_body: list[str] = []
    seen_heading = False

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            if seen_heading or current_body:
                sections.append((current_title, current_body))
            current_title = m.group(2).strip()
            current_body = []
            seen_heading = True
        else:
            current_body.append(line)
    sections.append((current_title, current_body))

    memories: list[ParsedMemory] = []
    for title, body_lines in sections:
        content = "\n".join(body_lines).strip()
        real_line_count = sum(1 for line in body_lines if line.strip())

        if real_line_count >= 2:
            memories.append(
                ParsedMemory(title=title, content=content, type="semantic", source_path=str(path))
            )
        elif content:
            if memories:
                memories[-1].content = (memories[-1].content + "\n\n" + content).strip()
            else:
                # nothing prior to merge into (first section of the file) -
                # it is kept as its own small memory.
                memories.append(
                    ParsedMemory(title=title, content=content, type="semantic", source_path=str(path))
                )

    return [mem for mem in memories if mem.content]


# --------------------------------------------------------------------------- dispatch


def parse_markdown_file(path: Path) -> list[ParsedMemory]:
    """Entry point: tries frontmatter -> MEMORY.md index -> generic, in that
    order. Reads the file only once."""
    text = path.read_text(encoding="utf-8")

    fm = parse_frontmatter_file(path, text)
    if fm is not None:
        return fm

    if is_memory_index(path, text):
        return parse_memory_index(path, text)

    return parse_generic_markdown(path, text)


def iter_markdown_files(root: Path) -> list[Path]:
    """`root` is a .md file -> [root]. `root` is a directory -> all *.md files
    recursively, stable order (by path)."""
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.md"))
