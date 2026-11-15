"""Trusted backend catalog entrypoint for this course package."""

from . import catalog_builder as _builder
from . import practice_catalog as _practice
from . import identity as _identity
from . import theory_helpers as _theory
from .catalog_builder import *  # noqa: F401,F403


def __getattr__(name: str):
    for module in (_builder, _practice, _identity, _theory):
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
