"""Repo-wide guard: everything tracked in the repo is written in English.

Spanish is allowed only where it is *data*, never prose or messages: the Spanish
evaluation datasets, test fixtures that exercise Spanish text handling, the
Context Engine's accent-folding table, the corpus context descriptions and the
author's name. Anything else that looks Spanish fails here.

Skipped when the repo checkout is not available (e.g. a PyInstaller build).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".sh", ".ps1", ".sql", ".txt", ".caddyfile", ""}

# Spanish as data (relative posix paths / path prefixes).
DATA_PATHS = (
    "packages/cerebro-memory/evals/README.md",  # quotes Spanish dataset examples
    "packages/cerebro-memory/evals/cases.yaml",
    "packages/cerebro-memory/evals/memories.yaml",
    "packages/cerebro-memory/evals/harness/adapters/cerebro_memory_adapter.py",  # corpus context descriptions
    "packages/cerebro-memory/evals/harness/adapters/naive_keyword.py",  # Spanish stopwords + accent table
    "packages/cerebro-memory/src/cerebro_memory/context_engine.py",  # accent folding table
    "packages/cerebro-memory/src/cerebro_memory/markdown_importer.py",  # dash regex
    "packages/cerebro-memory/tests/",  # Spanish fixtures
    "packages/cerebro-docs/tests/",  # slugify fixtures
    "packages/cerebro-cli/tests/test_repo_english.py",
    "packages/cerebro-cli/tests/test_english_only.py",
    "LICENSE",
)
ALLOWED_ACCENT_LINES = (
    "Sánchez",  # the author's name
)
ACCENTED = re.compile(r"[áéíóúñ¿¡ÁÉÍÓÚÑ]")
SPANISH_WORDS = re.compile(
    r"\b(que|para|cuando|donde|porque|desde|también|siempre|nunca|debe|puede|tiene|hay|"
    r"está|están|aquí|según|cada|entre|sobre|hacer|usuario|usuarios|archivo|archivos|"
    r"contraseña|ruta|carpeta|vacío|nuevo|nueva|además|ejemplo)\b",
    re.IGNORECASE,
)
# Words that are also valid in English prose / code identifiers are excluded by only
# flagging lines with at least two distinct Spanish hits or an accented character.


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []
    return [ROOT / f for f in out]


FILES = [
    p
    for p in _tracked_files()
    if p.suffix.lower() in TEXT_SUFFIXES
    and not any(p.relative_to(ROOT).as_posix().startswith(d) for d in DATA_PATHS)
    and "node_modules" not in p.parts
]

pytestmark = pytest.mark.skipif(not FILES, reason="repo checkout (git) not available")


def _lines(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    for n, line in enumerate(text.splitlines(), 1):
        yield n, line


def test_no_accented_characters_outside_data():
    bad = []
    for path in FILES:
        for n, line in _lines(path):
            if ACCENTED.search(line) and not any(a in line for a in ALLOWED_ACCENT_LINES):
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert not bad, "Spanish-looking (accented) text found:\n" + "\n".join(bad[:40])


def test_no_obvious_spanish_prose_outside_data():
    bad = []
    for path in FILES:
        for n, line in _lines(path):
            hits = {m.group(0).lower() for m in SPANISH_WORDS.finditer(line)}
            if len(hits) >= 2:
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert not bad, "Spanish-looking prose found:\n" + "\n".join(bad[:40])
