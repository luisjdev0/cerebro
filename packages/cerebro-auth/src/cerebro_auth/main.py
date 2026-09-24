"""uvicorn entrypoint: `python -m cerebro_auth.main` or `uvicorn cerebro_auth.main:app`."""

import logging

import uvicorn

from cerebro_auth.api import app  # noqa: F401  (re-exported for `uvicorn cerebro_auth.main:app`)
from cerebro_auth.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    settings = get_settings()
    uvicorn.run("cerebro_auth.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()
