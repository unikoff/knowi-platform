"""Gate/coordination tests with fake ports; signatures and PG are tested separately."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import time
from uuid import uuid4

import pytest

from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.privacy_control_v1 import AuthorityFrame, JournalEntry, VerifiedFrame, deletion_hash
from pymentor_analytics.contracts.privacy_storage_v1 import AppliedCursor, DeletionProgress
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.v2 import DeletionFence


def entry(seq=1):
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                            subject_ref=uuid4(), epoch=1, occurred_at=datetime.now(timezone.utc))
    return JournalEntry(seq=seq, producer="synthetic", control=control,
                        received_at=datetime.now(timezone.utc), payload_hash=deletion_hash("synthetic", control))


class Client:
    """Explicit fake verifier: no cryptographic verification is claimed here."""
    def __init__(self):
        self.config = SimpleNamespace(authority_id="authority")
        self.session = uuid4()
        self.hwm = 0
        self.barrier_advance = None
        self.fail = False
        self.entries = []
        self.calls = []

    def response(self, kind, after, through=None, entries=()):
        if self.fail:
            raise OSError("source unavailable")
        self.calls.append(kind)
        now = int(time.time())
        frame = AuthorityFrame(version=1, authority_id="authority", issuer="fixture", audience="analytics-privacy-journal",
                               kid="key", kind=kind, nonce=uuid4(), session=self.session, high_watermark=self.hwm,
                               after=after, through=through, scanned_through=after+len(entries), entries=tuple(entries),
                               issued_at=now, expires_at=now+3)
        mono = time.monotonic()
        return VerifiedFrame(frame, mono, mono)

    async def attach(self, after):
        self.session = uuid4()
        return self.response("attach", after)

    async def barrier(self, session):
        assert session == self.session
        if self.barrier_advance is not None:
            self.hwm = self.barrier_advance
        return self.response("barrier", 0)

    async def observe(self, session, after):
        assert session == self.session
        return self.response("observe", after)

    async def page(self, session, after, through, limit):
        assert session == self.session and limit == 100
        return self.response("page", after, through, [item for item in self.entries if after < item.seq <= through][:limit])


class Gate(PrivacyRuntime):
    def __init__(self, client, **kwargs):
        self.applied = 0
        super().__init__(client, SimpleNamespace(timeout=1), **kwargs)

    async def _read_cursor(self):
        return self.applied


def test_restored_cursor_cannot_open_new_process_without_fresh_attach_barrier():
    async def run():
        client = Client()
        gate = Gate(client)
        assert not await gate.is_ready()
        await gate.tick()
        assert client.calls == ["attach", "barrier", "observe"]
        assert await gate.is_ready()
        second = Gate(client)
        assert not await second.is_ready()
    asyncio.run(run())


def test_deletion_during_attach_handoff_is_in_opening_barrier():
    async def run():
        client = Client()
        client.barrier_advance = 1
        gate = Gate(client)
        await gate.tick()
        assert gate._initial_hwm == 0 and gate._opening_barrier == 1
        assert not await gate.is_ready()
        gate.applied = 1
        assert await gate.is_ready()
        client.hwm = 2
        client.barrier_advance = None
        await gate.tick()
        assert not await gate.is_ready()  # old B alone is insufficient
        gate.applied = 2
        assert await gate.is_ready()
    asyncio.run(run())


def test_expired_or_unavailable_authority_closes_even_when_cursor_matches():
    async def run():
        client = Client()
        gate = Gate(client)
        await gate.tick()
        value = gate._latest
        gate._latest = VerifiedFrame(value.frame, time.monotonic()-4, time.monotonic()-4)
        assert not await gate.is_ready()
        client.fail = True
        with pytest.raises(OSError):
            await gate.tick()
        assert gate._session is None and gate._latest is None
        assert not await gate.is_ready()
    asyncio.run(run())


def test_local_cursor_ahead_of_authority_never_opens():
    async def run():
        gate = Gate(Client())
        gate.applied = 1
        with pytest.raises(ValueError):
            await gate.tick()
        assert not await gate.is_ready()
    asyncio.run(run())


def test_controller_purges_bounded_chunks_without_premature_ack():
    async def run():
        client = Client()
        record = entry()
        client.hwm = 1
        client.entries = [record]
        class Store:
            calls = 0
            async def cursor(self, authority):
                assert authority == "authority"
                return AppliedCursor(authority, gate.applied)
            async def apply(self, authority, received, limit):
                assert authority == "authority" and received == record and limit == 100
                self.calls += 1
                gate.applied = 1 if self.calls == 2 else 0
                return DeletionProgress(1, record.control.subject_ref, 1, gate.applied, self.calls == 2)
        store = Store()
        gate = Gate(client, store=store)
        await gate.tick()
        assert store.calls == 1 and gate.applied == 0 and not await gate.is_ready()
        await gate.tick()
        assert store.calls == 2 and gate.applied == 1 and await gate.is_ready()
    asyncio.run(run())


def test_unknown_apply_outcome_requires_fresh_source_and_cursor_reconciliation():
    async def run():
        client = Client()
        record = entry()
        client.hwm = 1
        client.entries = [record]
        class Store:
            calls = 0
            async def cursor(self, authority):
                return AppliedCursor(authority, gate.applied)
            async def apply(self, authority, received, limit):
                self.calls += 1
                gate.applied = 1
                raise OSError("lost commit reply")
        store = Store()
        gate = Gate(client, store=store)
        with pytest.raises(OSError):
            await gate.tick()
        assert not await gate.is_ready()
        await gate.tick()
        assert await gate.is_ready() and store.calls == 1
        assert client.calls.count("attach") == 2
    asyncio.run(run())


def test_unverified_source_cannot_bind_a_new_database_authority():
    async def run():
        client = Client()
        client.fail = True
        class UnboundGate(Gate):
            async def _read_cursor(self): return None
        class Store:
            async def cursor(self, authority):
                raise AssertionError("unverified authority bound the database")
        gate = UnboundGate(client, store=Store())
        with pytest.raises(OSError):
            await gate.tick()
        assert not await gate.is_ready()
    asyncio.run(run())


def test_transaction_guard_rechecks_after_db_yield_and_at_commit():
    async def run():
        from pymentor_analytics.adapters.postgres.database import Database
        from pymentor_analytics.app.config import Settings
        client = Client()
        gate = Gate(client)
        await gate.tick()
        class Session:
            async def execute(self, statement):
                assert "FOR" not in str(statement)
                return SimpleNamespace(one_or_none=lambda: SimpleNamespace(authority_id="authority", applied_through=0))
        class Sessions:
            committed = False
            @asynccontextmanager
            async def begin(self):
                yield Session()
                self.committed = True
        database = Database(Settings(database_url="postgresql://synthetic@127.0.0.1:57392/analytics_test"))
        sessions = Sessions()
        database.sessions = sessions
        database.transaction_guard = gate.check_in_transaction
        database.transaction_exit_guard = gate.check_in_transaction
        try:
            with pytest.raises(PrivacyUnavailable):
                async with database.transaction():
                    gate._latest = None
            assert not sessions.committed
            with pytest.raises(PrivacyUnavailable):
                async with database.transaction():
                    raise AssertionError("closed barrier entered work")
        finally:
            await database.close()
    asyncio.run(run())


def test_stop_closes_gate_and_cancels_poll_without_background_leak():
    async def run():
        client = Client()
        client.fail = True
        gate = Gate(client, poll_seconds=0.05)
        gate.start()
        await asyncio.sleep(0)
        await asyncio.wait_for(gate.stop(), timeout=0.5)
        assert gate._task is None and not await gate.is_ready()
        with pytest.raises(PrivacyUnavailable):
            await gate.tick()
    asyncio.run(run())
