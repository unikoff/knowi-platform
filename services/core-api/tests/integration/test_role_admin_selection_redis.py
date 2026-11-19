"""Redis selection lease rehearsal in the dedicated disposable instance."""
import asyncio
import os
from uuid import uuid4

import pytest

from pymentor_core.adapters.runtime import RuntimeRedisSessionStore


def test_selection_lease_scope_ttl_and_revision_on_disposable_redis():
    url = os.environ.get('KNOWI_TEST_REDIS_URL')
    if not url:
        pytest.skip('KNOWI_TEST_REDIS_URL required')
    if url != 'redis://127.0.0.1:57381/0':
        raise ValueError('Redis target outside disposable allowlist')
    async def exercise():
        store = RuntimeRedisSessionStore(url, prefix='r4-selection-' + uuid4().hex + ':')
        actor, target = str(uuid4()), str(uuid4())
        key = store._role_selection_key(actor, 'backend', target)
        try:
            assert await store.get_role_selection(actor, 'backend', target) is None
            await store.store_role_selection(actor, 'backend', target, 4)
            assert await store.get_role_selection(actor, 'backend', target) == 4
            assert 1 <= await store._client.ttl(key) <= 900
            assert await store.get_role_selection(str(uuid4()), 'backend', target) is None
            assert await store.get_role_selection(actor, 'backend_copy', target) is None
            assert await store.get_role_selection(actor, 'backend', str(uuid4())) is None
            await store.store_role_selection(actor, 'backend', target, 5)
            assert await store.get_role_selection(actor, 'backend', target) == 5
        finally:
            await store._client.delete(key)
            await store.close()
    asyncio.run(exercise())
