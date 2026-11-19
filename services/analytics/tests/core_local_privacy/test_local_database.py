import asyncio
from contextlib import asynccontextmanager

import pytest
from pymentor_analytics.adapters.postgres.database import Database


def test_transaction_default_entry_only_and_opt_in_exit_guard():
    async def run():
        class Sessions:
            committed = False
            @asynccontextmanager
            async def begin(self):
                yield object()
                self.committed = True
        database = Database.__new__(Database)
        database.timeout = 2
        database.sessions = Sessions()
        seen = []
        async def entry(session): seen.append("entry")
        async def exit_guard(session): seen.append("exit")
        database.transaction_guard = entry
        database.transaction_exit_guard = None
        async with database.transaction(): seen.append("work")
        assert seen == ["entry", "work"]
        seen.clear()
        database.transaction_exit_guard = exit_guard
        async with database.transaction(): seen.append("work")
        assert seen == ["entry", "work", "exit"]
        seen.clear()
        database.sessions.committed = False
        async def closed(session): raise RuntimeError("barrier closed")
        database.transaction_exit_guard = closed
        with pytest.raises(RuntimeError):
            async with database.transaction(): seen.append("work")
        assert seen == ["entry", "work"] and not database.sessions.committed
    asyncio.run(run())
