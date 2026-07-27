"""Shared helpers for the rustruct test suite."""


def u(name, kind, **opts):
    """Build a Field tuple: (name, kind, opts)."""
    return (name, kind, opts)
