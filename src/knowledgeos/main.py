"""uvicorn entrypoint: `python -m knowledgeos.main` or `uvicorn knowledgeos.main:app`."""

import logging

import uvicorn

from knowledgeos.api import app  # noqa: F401  (re-exported for `uvicorn knowledgeos.main:app`)
from knowledgeos.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    settings = get_settings()
    uvicorn.run("knowledgeos.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()
