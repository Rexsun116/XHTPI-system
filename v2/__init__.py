"""Clean V2 runtime, isolated from the archived V1 model lineage."""

from .app import create_app

__all__ = ["create_app"]
