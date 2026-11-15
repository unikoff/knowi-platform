"""Shared, guarded PostgreSQL fixtures for business and transaction tests.

IdentityStub isolates these tests from authentication, which has its own real
PostgreSQL/Redis HTTP suite. It never creates or reads SQL session cookies.
"""
from types import SimpleNamespace
import sys

from pymentor_core.modules.identity.contracts import InvalidSession
from pymentor_core.modules.identity.api.sessions import _local_async_identity as _serving_identity
from test_c2_1_4_auth_legacy import runtime_db  # noqa: F401 (pytest fixture)


class IdentityStub:
    def __init__(self, tokens):
        self.tokens = tokens

    async def authenticate(self, token):
        if token not in self.tokens:
            raise InvalidSession("synthetic unknown token")
        return SimpleNamespace(user_id=self.tokens[token])


class PatchedContent:
    """Resolve the fixture's patched catalog functions at request time."""
    def __init__(self, module):
        self.module = module

    def __getattr__(self, name):
        return getattr(self.module, name)


def attach_identity(app, tokens):
    app.state.identity_sessions = IdentityStub(tokens)
    if getattr(app.state, "composition_root", None) is None:
        from pymentor_core.app.wiring import build_default_composition
        app.state.composition_root = build_default_composition()
    # These repository/UoW fixtures supply synchronous request sessions. Keep
    # their existing business seam; real asyncpg/Redis has separate runtime tests.
    # The stub is never a serving RuntimeIdentitySessions implementation.
    for name, module in list(sys.modules.items()):
        if name.startswith("pymentor_core.") and module is not None:
            if vars(module).get("_local_async_identity") is _serving_identity:
                module._local_async_identity = _business_identity


def _business_identity(request):
    identity = _serving_identity(request)
    return None if isinstance(identity, IdentityStub) else identity
