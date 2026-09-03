"""Clean V2 runtime, isolated from the archived V1 model lineage."""


def create_app(*args, **kwargs):
    from .app import create_app as factory
    return factory(*args, **kwargs)


__all__ = ["create_app"]
