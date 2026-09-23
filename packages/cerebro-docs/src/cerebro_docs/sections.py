"""Parsing of sections by heading and applying partial patches to a document's
Markdown content (PATCH /documents/{id}/section,
ecosistema-cerebro.md SS12).

Section = from a heading to the next heading of the SAME LEVEL OR HIGHER (a
'##' closes at the next '##' or '#', but never at a '###' nested inside it).
This heading detection deliberately DUPLICATES the logic of
`cerebro_memory.markdown_importer.HEADING_RE` (an ecosystem design decision: no
shared package for something this small, see ecosistema-cerebro.md SS14) - it's
generalized here to levels 1-6 instead of 1-2, because cerebro-docs stores full
documents and not just top-level distilled memories.

Heading not found -> HeadingNotFoundError, unless `create_if_missing=True`.
Duplicate (ambiguous) heading -> AmbiguousHeadingError ALWAYS, even with
`create_if_missing=True` - it never guesses which of the duplicates is the target,
same criterion as Claude Code's `Edit` tool with `old_string`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

# Defensive normalization (luisjdev-pendientes/ecosistema-cerebro, "docs_patch_section
# reports update failures"): if the caller passes the heading WITH the markdown
# prefix included (e.g. "## My section" instead of "My section"), find_section will
# never match it -- the titles find_headings parses already come without the prefix
# (HEADING_RE.group(2)). Trimming it here avoids that confusing error without
# changing the contract: a real heading that starts with a literal "#" (not as a
# level prefix, but as text) still can't be represented, but that case hasn't been
# seen in practice and HEADING_RE wouldn't support it either when parsing the document.
_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s+")

Operation = Literal["replace", "append", "insert_after", "insert_before", "delete"]
VALID_OPERATIONS: tuple[Operation, ...] = ("replace", "append", "insert_after", "insert_before", "delete")


class SectionError(ValueError):
    """Base for section parsing/patching errors."""


class HeadingNotFoundError(SectionError):
    """The requested heading doesn't exist in the document (and create_if_missing is False)."""


class AmbiguousHeadingError(SectionError):
    """The requested heading appears more than once - it's never guessed which one."""


class InvalidOperationError(SectionError):
    """`operation` outside the valid vocabulary (VALID_OPERATIONS)."""


@dataclass(frozen=True)
class HeadingMatch:
    level: int         # 1-6
    title: str
    start_line: int    # first line of the section (the heading itself), 0-indexed
    end_line: int       # EXCLUSIVE line where the section ends (next heading of level <=, or EOF)


def find_headings(content: str) -> list[HeadingMatch]:
    """All the headings in the document, with their section range already resolved."""
    lines = content.splitlines()
    raw: list[tuple[int, int, str]] = []  # (line_index, level, title)
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            raw.append((i, len(m.group(1)), m.group(2).strip()))

    matches: list[HeadingMatch] = []
    for idx, (line_index, level, title) in enumerate(raw):
        end = len(lines)
        for later_index, later_level, _later_title in raw[idx + 1 :]:
            if later_level <= level:
                end = later_index
                break
        matches.append(HeadingMatch(level=level, title=title, start_line=line_index, end_line=end))
    return matches


def find_section(content: str, heading: str) -> HeadingMatch:
    """The heading must match EXACTLY (the text as-is, without the `#`s). Unique or fails
    - see AmbiguousHeadingError/HeadingNotFoundError."""
    matches = [h for h in find_headings(content) if h.title == heading]
    if not matches:
        raise HeadingNotFoundError(heading)
    if len(matches) > 1:
        raise AmbiguousHeadingError(heading)
    return matches[0]


def apply_section_patch(
    content: str,
    *,
    heading: str,
    operation: Operation,
    body: str = "",
    create_if_missing: bool = False,
    new_heading_level: int = 2,
) -> str:
    """Applies `operation` to the `heading` section and returns the full resulting
    content.

    - `replace`: replaces the section's BODY (everything following the heading line,
      up to the next heading of the same level or higher) with `body`. The
      heading line doesn't change.
    - `append`: appends `body` to the end of the section's body, before the next
      heading.
    - `insert_after` / `insert_before`: inserts `body` (raw markdown, may bring its
      own heading) as a sibling block right after/before the entire section
      (heading included).
    - `delete`: removes the entire section (heading included).

    If the heading doesn't exist and `create_if_missing=True`, a new section is
    appended to the end of the document (level `new_heading_level`) with `body` as
    its content - except for `delete`, where there's nothing to delete and the
    content comes back unchanged.
    An AMBIGUOUS (duplicate) heading always fails, even with `create_if_missing`.
    """
    if operation not in VALID_OPERATIONS:
        raise InvalidOperationError(operation)

    heading = _HEADING_PREFIX_RE.sub("", heading.strip(), count=1).strip()

    lines = content.splitlines()

    try:
        section = find_section(content, heading)
    except HeadingNotFoundError:
        if not create_if_missing:
            raise
        if operation == "delete":
            return content
        new_block = f"{'#' * new_heading_level} {heading}\n{body}".rstrip("\n")
        if content.strip():
            new_content = content.rstrip("\n") + "\n\n" + new_block
        else:
            new_content = new_block
        return new_content + "\n"

    if operation == "delete":
        new_lines = lines[: section.start_line] + lines[section.end_line :]

    elif operation == "replace":
        heading_line = lines[section.start_line]
        new_lines = lines[: section.start_line] + [heading_line] + body.splitlines() + lines[section.end_line :]

    elif operation == "append":
        section_body_lines = lines[section.start_line + 1 : section.end_line]
        while section_body_lines and section_body_lines[-1].strip() == "":
            section_body_lines.pop()
        new_lines = (
            lines[: section.start_line + 1]
            + section_body_lines
            + [""]
            + body.splitlines()
            + lines[section.end_line :]
        )

    elif operation == "insert_after":
        new_lines = lines[: section.end_line] + [""] + body.splitlines() + lines[section.end_line :]

    else:  # insert_before
        new_lines = lines[: section.start_line] + body.splitlines() + [""] + lines[section.start_line :]

    result = "\n".join(new_lines)
    return result + "\n" if content.endswith("\n") else result
