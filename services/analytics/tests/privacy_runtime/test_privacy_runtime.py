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


def test_matching_rolled_back_authority_and_cursor_stay_closed_without_permit():
    async def run():
        opened = False
        gate = Gate(Client(), recovery_permit=lambda: opened)
        await gate.tick()
        assert not await gate.is_ready()
        with pytest.raises(PrivacyUnavailable):
            await gate.check_in_transaction(None)
        opened = True
        assert await gate.is_ready()
        opened = False
        assert not await gate.is_ready()
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


@pytest.mark.parametrize(("failure", "sql_count"), [
    (None, 3), ("facts_acl", 1), ("fences_acl", 2), ("cursor_acl", 3), ("missing", 3), ("identity", 3),
    ("bad_type", 3), ("ahead", 3), ("behind", 3), ("restore_closed", 0), ("expired_frame", 3),
])
def test_transaction_readiness_uses_original_sql_and_current_proof(failure, sql_count):
    statements = [
        "SELECT 1 FROM analytics.activity_facts LIMIT 1",
        "SELECT 1 FROM analytics.subject_fences LIMIT 1",
        "SELECT authority_id,applied_through FROM analytics.privacy_state WHERE singleton=true",
    ]
    background_sessions = []
    permitted = True

    class Session:
        def __init__(self, failure=None):
            self.failure = failure
            self.statements = []
        async def execute(self, statement):
            self.statements.append(str(statement))
            if self.failure == {1: "facts_acl", 2: "fences_acl", 3: "cursor_acl"}[len(self.statements)]:
                raise PermissionError("query role cannot read")
            applied = {"bad_type": True, "ahead": 3, "behind": 1}.get(self.failure, 2)
            authority_id = "unexpected" if self.failure == "identity" else "authority"
            row = SimpleNamespace(authority_id=authority_id, applied_through=applied)
            return SimpleNamespace(one_or_none=lambda: None if self.failure == "missing" else row)

    class Sessions:
        @asynccontextmanager
        async def begin(self):
            session = Session()
            background_sessions.append(session)
            yield session

    async def run():
        nonlocal permitted
        client = Client()
        client.hwm = 2
        runtime = PrivacyRuntime(client, SimpleNamespace(timeout=1, sessions=Sessions()),
                                 recovery_permit=lambda: permitted)
        await runtime.tick()
        assert background_sessions[0].statements == statements
        if failure == "restore_closed":
            permitted = False
        if failure == "expired_frame":
            previous = runtime._latest
            expired = time.monotonic() - 4
            runtime._latest = VerifiedFrame(previous.frame, expired, expired)

        active = Session(failure)
        if failure is None:
            await runtime.check_readiness_in_transaction(active)
        else:
            expected = PermissionError if failure.endswith("_acl") else PrivacyUnavailable
            with pytest.raises(expected):
                await runtime.check_readiness_in_transaction(active)
        assert active.statements == statements[:sql_count]
        assert len(background_sessions) == 1

    asyncio.run(run())


def test_poll_and_singleflight_readiness_reach_live_cursor_before_queued_query_transactions():
    from pymentor_analytics.adapters.postgres.database import Database
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.probes import SingleflightReadiness

    async def run():
        database = Database(Settings(database_url="postgresql://synthetic@127.0.0.1:57392/analytics_test"))
        database.transaction_admission = asyncio.Semaphore(2)
        free_connections = asyncio.Queue(maxsize=2)
        for connection in range(2):
            free_connections.put_nowait(connection)
        timeline = []
        waiting = asyncio.Event()
        first_two = asyncio.Event()
        poll_waiting = asyncio.Event()
        readiness_waiting = asyncio.Event()
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        requests_started = 0
        requests_admitted = 0
        readiness_checkouts = 0

        class Session:
            async def execute(self, statement):
                sql = str(statement)
                phase = asyncio.current_task().get_name()
                if phase in {"poll", "readiness"} and "analytics." in sql:
                    timeline.append((phase, sql))
                    await asyncio.sleep(0)  # Model a PostgreSQL round trip.
                if "analytics.privacy_state" in sql:
                    row = SimpleNamespace(authority_id="authority", applied_through=0)
                    return SimpleNamespace(one_or_none=lambda: row)

        class Sessions:
            @asynccontextmanager
            async def begin(self):
                nonlocal readiness_checkouts
                phase = asyncio.current_task().get_name()
                if phase == "poll":
                    poll_waiting.set()
                if phase == "readiness":
                    readiness_checkouts += 1
                    readiness_waiting.set()
                connection = await free_connections.get()
                try:
                    yield Session()
                finally:
                    free_connections.put_nowait(connection)

        database.sessions = Sessions()
        client = Client()
        runtime = PrivacyRuntime(client, database)
        await runtime.tick()
        database.transaction_guard = runtime.check_in_transaction
        database.transaction_exit_guard = runtime.check_in_transaction

        class DayReadiness:
            async def is_ready(self):
                asyncio.current_task().set_name("readiness")
                async with database.query_readiness_transaction() as session:
                    await runtime.check_readiness_in_transaction(session)
                return True

        readiness = SingleflightReadiness(DayReadiness(), timeout=1)

        async def request():
            nonlocal requests_started, requests_admitted
            requests_started += 1
            if requests_started == 33:
                waiting.set()
            async with database.transaction():
                requests_admitted += 1
                if requests_admitted == 1:
                    await release_first.wait()
                elif requests_admitted == 2:
                    first_two.set()
                    await release_second.wait()

        tasks = [asyncio.create_task(request()) for _ in range(33)]
        poll = None
        waiters = []
        try:
            async with asyncio.timeout(1):
                await waiting.wait()
                await first_two.wait()
                poll = asyncio.create_task(runtime.tick(), name="poll")
                await poll_waiting.wait()
                waiters = [asyncio.create_task(readiness.is_ready()) for _ in range(33)]
                await readiness_waiting.wait()
                assert readiness_checkouts == 1
                assert requests_admitted == 2 and free_connections.empty()
                tasks[-1].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[-1]
                release_first.set()
                await poll
                assert all(await asyncio.gather(*waiters))
                assert readiness_checkouts == 1
                poll_sql = [sql for phase, sql in timeline if phase == "poll"]
                readiness_sql = [sql for phase, sql in timeline if phase == "readiness"]
                assert len(poll_sql) == 3
                assert "analytics.activity_facts" in poll_sql[0]
                assert "analytics.subject_fences" in poll_sql[1]
                assert "analytics.privacy_state" in poll_sql[2]
                assert readiness_sql == [poll_sql[2], *poll_sql, poll_sql[2]]
                assert client.calls == ["attach", "barrier", "observe", "observe"]
                release_second.set()
                await asyncio.gather(*tasks[:-1])
                assert requests_admitted == 32
                assert free_connections.qsize() == 2
                assert await readiness.is_ready()  # A new request opens a new transaction.
                assert readiness_checkouts == 2

                client.fail = True
                with pytest.raises(OSError):
                    await runtime.tick()
                with pytest.raises(PrivacyUnavailable):
                    async with database.transaction():
                        raise AssertionError("closed authority allowed new Query work")
        finally:
            pending = [*tasks, *waiters, *((poll,) if poll is not None else ())]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await readiness.close()
            await database.close()
    asyncio.run(run())


def test_query_admission_wait_uses_transaction_deadline_and_does_not_leak():
    from pymentor_analytics.adapters.postgres.database import Database
    from pymentor_analytics.app.config import Settings

    async def run():
        database = Database(Settings(database_url="postgresql://synthetic@127.0.0.1:57392/analytics_test"))
        database.transaction_admission = asyncio.Semaphore(1)
        held, release = asyncio.Event(), asyncio.Event()

        class Sessions:
            @asynccontextmanager
            async def begin(self):
                yield object()

        database.sessions = Sessions()

        async def hold():
            async with database.transaction():
                held.set()
                await release.wait()

        holder = asyncio.create_task(hold())
        try:
            await asyncio.wait_for(held.wait(), timeout=1)
            database.timeout = .05
            with pytest.raises(TimeoutError):
                async with database.transaction():
                    raise AssertionError("pending Query entered a full pool")
            release.set()
            await holder
            async with database.transaction():
                pass
        finally:
            holder.cancel()
            await asyncio.gather(holder, return_exceptions=True)
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
