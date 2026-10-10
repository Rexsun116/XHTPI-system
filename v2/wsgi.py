"""Production WSGI entrypoint for an explicitly configured V2 runtime."""

from .app import create_configured_app


app = create_configured_app()
