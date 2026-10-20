"""ASGI entrypoint (cli.md §1): ``uvicorn ak5.server.app:app`` / ``create_app()``."""

from ak5.server.main import app, create_app

__all__ = ["app", "create_app"]
