"""Parsing puro para el importador de Markdown (Fase 5, conector 1, plan_v2.md SS8).

Primer conector de Fase 5 a propósito: importar `MEMORY.md`/`CLAUDE.md`/notas sueltas
ya existentes es lo que resuelve la migración desde el statu quo del usuario (mismo
argumento que plan_v2.md SS8/SS11 usan para elegirlo como conector #1).

Todo lo de este módulo es parsing puro (solo lee archivos del filesystem, no habla con
la API ni con Postgres) para que sea testeable sin nada más que fixtures en disco - ver
tests/test_markdown_importer.py. La orquestación (dedup por búsqueda, POST /memories,
manejo de 422 por credenciales, reporte final) vive en `knowledgeos.cli`.

Tres formatos reconocidos, en este orden de preferencia (`parse_markdown_file`):

    1. Frontmatter YAML estilo memoria de Claude Code (`name`, `description`,
       `metadata.type`) -> una sola memoria por archivo.
    2. Índice `MEMORY.md` (líneas `- [título](archivo.md) — hook`) -> sigue los links
       si los archivos existen (recursivamente, vía parse_markdown_file); si no,
       cada bullet se vuelve su propia memoria pequeña.
    3. Markdown genérico -> se divide por headings de nivel 1-2; cada sección con >= 2
       líneas de contenido real es una memoria, las más pequeñas se agrupan con la
       anterior.

En los tres casos, bloques de código de más de 30 líneas se truncan a
"[código truncado]" antes de cualquier otro procesamiento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CODE_BLOCK_MAX_LINES = 30
CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*\r?\n?(.*)\Z", re.DOTALL)

# `- [título](archivo.md) — hook` (el guion largo/corto del hook es opcional).
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
    """Reemplaza el contenido interno de cualquier bloque ``` ``` de más de
    `max_lines` líneas por "[código truncado]", conservando las vallas."""

    def _replace(m: re.Match[str]) -> str:
        block = m.group(0)
        inner = m.group(1)
        inner_lines = inner.splitlines()
        if len(inner_lines) <= max_lines:
            return block
        fence_line = block.splitlines()[0]  # p.ej. "```python"
        return f"{fence_line}\n[código truncado]\n```"

    return CODE_BLOCK_RE.sub(_replace, text)


# --------------------------------------------------------------------------- 1. frontmatter


def parse_frontmatter_file(path: Path, text: str) -> list[ParsedMemory] | None:
    """Frontmatter YAML estilo memoria de Claude Code. Devuelve None (no aplica, que
    el llamador intente el siguiente formato) si no hay frontmatter parseable o si no
    tiene la forma esperada (falta `name`)."""
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
    """Heurística: se llama MEMORY.md (sin distinguir mayúsculas) Y contiene al menos
    una línea `- [título](archivo)`."""
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

        # No existe el archivo (o quedó vacío tras parsear): el bullet mismo se
        # vuelve una memoria pequeña.
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
                # nada anterior a lo que fusionar (primera seccion del archivo) -
                # se conserva como su propia memoria pequeña.
                memories.append(
                    ParsedMemory(title=title, content=content, type="semantic", source_path=str(path))
                )

    return [mem for mem in memories if mem.content]


# --------------------------------------------------------------------------- dispatch


def parse_markdown_file(path: Path) -> list[ParsedMemory]:
    """Punto de entrada: intenta frontmatter -> índice MEMORY.md -> genérico, en ese
    orden. Lee el archivo una sola vez."""
    text = path.read_text(encoding="utf-8")

    fm = parse_frontmatter_file(path, text)
    if fm is not None:
        return fm

    if is_memory_index(path, text):
        return parse_memory_index(path, text)

    return parse_generic_markdown(path, text)


def iter_markdown_files(root: Path) -> list[Path]:
    """`root` es un archivo .md -> [root]. `root` es un directorio -> todos los *.md
    recursivamente, orden estable (por ruta)."""
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.md"))
