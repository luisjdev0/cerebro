"""uvicorn entrypoint: `python -m cerebro_docs.main` or `uvicorn cerebro_docs.main:app`."""

import logging

import uvicorn

from cerebro_docs.api import app  # noqa: F401  (re-exported for `uvicorn cerebro_docs.main:app`)
from cerebro_docs.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    settings = get_settings()
    uvicorn.run("cerebro_docs.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()
