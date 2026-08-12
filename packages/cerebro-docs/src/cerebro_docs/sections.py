"""Parseo de secciones por heading y aplicacion de parches parciales sobre el
contenido Markdown de un documento (PATCH /documents/{id}/section,
ecosistema-cerebro.md SS12).

Seccion = desde un heading hasta el siguiente heading del MISMO NIVEL O SUPERIOR (un
'##' cierra en el proximo '##' o '#', pero nunca en un '###' anidado dentro de el).
Esta deteccion de headings DUPLICA a proposito la logica de
`cerebro_memory.markdown_importer.HEADING_RE` (decision de diseno del ecosistema: sin
paquete compartido para algo tan chico, ver ecosistema-cerebro.md SS14) - se
generaliza aqui a niveles 1-6 en vez de 1-2, porque cerebro-docs guarda documentos
completos y no solo memorias destiladas de nivel superior.

Heading no encontrado -> HeadingNotFoundError, salvo `create_if_missing=True`.
Heading duplicado (ambiguo) -> AmbiguousHeadingError SIEMPRE, incluso con
`create_if_missing=True` - nunca se adivina cual de los duplicados es el objetivo,
mismo criterio que la tool `Edit` de Claude Code con `old_string`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

Operation = Literal["replace", "append", "insert_after", "insert_before", "delete"]
VALID_OPERATIONS: tuple[Operation, ...] = ("replace", "append", "insert_after", "insert_before", "delete")


class SectionError(ValueError):
    """Base de los errores de parseo/parche de secciones."""


class HeadingNotFoundError(SectionError):
    """El heading pedido no existe en el documento (y create_if_missing es False)."""


class AmbiguousHeadingError(SectionError):
    """El heading pedido aparece mas de una vez - nunca se adivina cual."""


class InvalidOperationError(SectionError):
    """`operation` fuera del vocabulario valido (VALID_OPERATIONS)."""


@dataclass(frozen=True)
class HeadingMatch:
    level: int         # 1-6
    title: str
    start_line: int    # primera linea de la seccion (el heading mismo), 0-indexed
    end_line: int       # linea EXCLUSIVA donde termina la seccion (siguiente heading de nivel <=, o EOF)


def find_headings(content: str) -> list[HeadingMatch]:
    """Todos los headings del documento, con el rango de su seccion ya resuelto."""
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
    """El heading debe matchear EXACTO (el texto tal cual, sin los `#`). Unico o falla
    - ver AmbiguousHeadingError/HeadingNotFoundError."""
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
    """Aplica `operation` sobre la seccion de `heading` y devuelve el contenido
    completo resultante.

    - `replace`: sustituye el CUERPO de la seccion (todo lo que sigue a la linea del
      heading, hasta el siguiente heading del mismo nivel o superior) por `body`. La
      linea del heading no cambia.
    - `append`: agrega `body` al final del cuerpo de la seccion, antes del siguiente
      heading.
    - `insert_after` / `insert_before`: inserta `body` (markdown crudo, puede traer su
      propio heading) como bloque hermano justo despues/antes de la seccion completa
      (heading incluido).
    - `delete`: elimina la seccion completa (heading incluido).

    Si el heading no existe y `create_if_missing=True`, se agrega una seccion nueva al
    final del documento (nivel `new_heading_level`) con `body` como cuerpo - salvo
    para `delete`, donde no hay nada que borrar y el contenido vuelve sin cambios.
    Un heading AMBIGUO (duplicado) siempre falla, incluso con `create_if_missing`.
    """
    if operation not in VALID_OPERATIONS:
        raise InvalidOperationError(operation)

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
