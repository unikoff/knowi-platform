"""Generic compatibility facade for trusted course-owned catalog helpers."""

from __future__ import annotations

from .runtime import default_runtime


def _catalog():
    return default_runtime().snapshot("backend").catalog


def __getattr__(name: str):
    try:
        return getattr(_catalog(), name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__():
    return sorted(set(globals()) | set(dir(_catalog())))
