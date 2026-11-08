"""The CLI is English-only (repo convention): guard against Spanish creeping back
into user-facing strings."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "cerebro_cli"
ACCENTED = re.compile(r"[áéíóúñ¿¡ÁÉÍÓÚÑ]")
SPANISH_WORDS = re.compile(
    r"\b(Error: no|no existe|todavia|sin |Crea |Lista |Borra |Guarda |Sesion|contrasena|"
    r"usuario[s]?\b|archivo[s]?\b|desde el|para el|solo |nunca |ruta )",
)


def _strings():
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(getattr(n.body[0], "value", None), ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                yield path.name, node.lineno, node.value


def test_no_accented_characters_in_user_facing_strings():
    bad = [(f, n, s) for f, n, s in _strings() if ACCENTED.search(s)]
    assert not bad, bad


def test_no_obvious_spanish_words_in_user_facing_strings():
    bad = [(f, n, s) for f, n, s in _strings() if SPANISH_WORDS.search(s)]
    assert not bad, bad
