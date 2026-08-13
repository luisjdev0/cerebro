"""Carga minima de `.env.production`/`.env` de la raiz del monorepo, sin pisar
variables ya presentes en el entorno del proceso.

Por que esto vive aqui: antes de la migracion a `cerebro-cli`, el usuario tenia un
wrapper `.venv\\Scripts\\cerebro.cmd` que el (no esta parser.py) generaba a mano --
cargaba `.env.production` (que apunta el CLI al VPS via
`KNOWLEDGEOS_API_URL`/`KNOWLEDGEOS_API_TOKEN`, legado que `cerebro_clients.config`
sigue aceptando como fallback) y despues delegaba en el binario real. Al instalar
`cerebro-cli` con `pip install -e`, el entry point `cerebro.exe` que setuptools genera
gana en el PATH sobre ese `.cmd` (PATHEXT resuelve `.exe` antes que `.cmd`), asi que el
wrapper deja de ejecutarse aunque siga presente -- sin este modulo, `cerebro memory
stats` dejaria de hablar con el VPS por defecto sin que el usuario haya cambiado nada.
Este modulo reproduce esa carga DENTRO del propio CLI para que el comportamiento no
dependa de un wrapper de shell generado a mano.

Solo tiene efecto en instalacion editable dentro del monorepo (donde `packages/`
existe relativo a este archivo): si `cerebro-cli` se instalara fuera de este repo (p.ej.
publicado en un indice), `.env.production`/`.env` simplemente no se encontrarian bajo
`REPO_ROOT` y `load_repo_dotenv()` no haria nada -- no es un requisito de despliegue,
es una comodidad para el entorno de desarrollo actual del usuario.

Parser deliberadamente minimo (sin dependencia nueva tipo python-dotenv): una linea
por variable, `CLAVE=valor`, comentarios con `#` (linea completa o al final tras
espacio) y lineas vacias se ignoran. No soporta interpolacion de variables ni
multilinea -- ninguno de los .env de este repo los necesita.
"""

from __future__ import annotations

import os
from pathlib import Path

# packages/cerebro-cli/src/cerebro_cli/dotenv.py -> parents[4] es la raiz del
# monorepo (mismo calculo que REPO_ROOT en shared_commands.py).
REPO_ROOT = Path(__file__).resolve().parents[4]


def parse_dotenv(text: str) -> dict[str, str]:
    """Parsea el contenido de un archivo .env a un dict, en el orden en que aparecen
    las claves. Lineas vacias y comentarios (`#...`) se ignoran; una linea sin `=` se
    ignora tambien (no es un par clave/valor valido)."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # comillas envolventes opcionales, como en un .env tipico
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def _apply_without_overwriting(values: dict[str, str], env: dict[str, str]) -> None:
    for key, value in values.items():
        env.setdefault(key, value)


def load_repo_dotenv(repo_root: Path | None = None, *, env: dict[str, str] | None = None) -> None:
    """Carga `.env.production` y luego `.env` desde la raiz del monorepo, SIN pisar
    ninguna variable ya presente en `env` (default: `os.environ`) -- ni la que ya
    estaba puesta antes de llamar esta funcion, ni la que ya puso `.env.production` al
    procesarse primero. Por eso el orden importa: `.env.production` gana sobre `.env`
    cuando ambos definen la misma clave, y cualquier variable exportada a mano por el
    usuario gana sobre ambos.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    target_env = env if env is not None else os.environ

    for filename in (".env.production", ".env"):
        path = root / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        _apply_without_overwriting(parse_dotenv(text), target_env)
