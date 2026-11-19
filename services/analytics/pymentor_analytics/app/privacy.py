"""Fresh process-local authority barrier and one bounded deletion step per tick."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from sqlalchemy import text

from pymentor_analytics.contracts.privacy_control_v1 import VerifiedFrame
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable, RestoreProof


class PrivacyRuntime:
    """No persisted ready flag or restored watermark can open a new process.

    Readers observe the independent authority and the applied DB prefix. Only
    the dedicated privacy-role process receives a deletion store. Runtime
    transactions recheck this gate before work and before committing/materializing.
    """
    def __init__(self, client, database, *, store=None, poll_seconds=1.0):
        if not 0.05 <= poll_seconds <= 1.0:
            raise ValueError("invalid privacy poll bound")
        self.client = client
        self.database = database
        self.store = store
        self.authority_id = client.config.authority_id
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._task = None
        self._session = None
        self._initial_hwm = None
        self._opening_barrier = None
        self._latest = None

    def start(self):
        if self._task is not None or self._stop.is_set():
            raise RuntimeError("privacy runtime already started or stopped")
        self._task = asyncio.create_task(self._run(), name="analytics-privacy-barrier")

    async def stop(self):
        self._stop.set()
        self._close()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=2)
            self._task = None

    def _close(self):
        self._latest = None
        self._session = None
        self._initial_hwm = None
        self._opening_barrier = None

    def _accept(self, value):
        if (not isinstance(value, VerifiedFrame) or not value.is_fresh()
                or value.frame.authority_id != self.authority_id
                or (self._session is not None and value.frame.session != self._session)):
            raise PrivacyUnavailable()
        return value

    async def _read_cursor(self):
        # Intentionally bypass Database.transaction's operation guard to avoid
        # recursively asking the same gate. No row lock: state→subject locking
        # would deadlock with privacy's subject→state progress transaction.
        async with asyncio.timeout(self.database.timeout):
            async with self.database.sessions.begin() as session:
                await session.execute(text("SELECT 1 FROM analytics.activity_facts LIMIT 1"))
                await session.execute(text("SELECT 1 FROM analytics.subject_fences LIMIT 1"))
                row = (await session.execute(text("SELECT authority_id,applied_through FROM analytics.privacy_state WHERE singleton=true"))).one_or_none()
        if row is None:
            return None
        if row.authority_id != self.authority_id or type(row.applied_through) is not int or row.applied_through < 0:
            raise PrivacyUnavailable()
        return row.applied_through

    async def tick(self):
        """One bounded transport/storage iteration, also usable by scenario tests."""
        if self._stop.is_set():
            raise PrivacyUnavailable()
        try:
            applied = await self._read_cursor()
            if applied is None:
                # Missing/restored state is never initialized from configuration
                # alone. Authenticate a current authority observation first.
                self._close()
                applied = 0
            if self._session is None:
                attached = self._accept(await self.client.attach(applied))
                self._session = attached.frame.session
                self._initial_hwm = attached.frame.high_watermark
                barrier = self._accept(await self.client.barrier(self._session))
                if barrier.frame.high_watermark < self._initial_hwm:
                    raise PrivacyUnavailable()
                self._opening_barrier = barrier.frame.high_watermark
                self._latest = barrier
            if self.store is not None:
                local = await self.store.cursor(self.authority_id)
                applied = local.applied_through
            observed = self._accept(await self.client.observe(self._session, applied))
            if observed.frame.high_watermark < self._opening_barrier or applied > observed.frame.high_watermark:
                raise PrivacyUnavailable()
            self._latest = observed
            if self.store is not None and applied < observed.frame.high_watermark:
                page = self._accept(await self.client.page(self._session, applied, observed.frame.high_watermark, 100))
                if not page.frame.entries or page.frame.entries[0].seq != applied + 1:
                    raise PrivacyUnavailable()
                # Purge may require many ticks. Cursor/ACK advances only after
                # every obligation is actually gone, never from a received page.
                await self.store.apply(self.authority_id, page.frame.entries[0], 100)
                self._latest = page
        except BaseException:
            self._close()
            raise

    async def _run(self):
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # fail closed; errors never log UUIDs, frames or credentials
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    def _proof(self, applied):
        value = self._latest
        if (value is None or not value.is_fresh() or self._session is None
                or self._opening_barrier is None or self._initial_hwm is None
                or value.frame.session != self._session
                or value.frame.high_watermark < self._opening_barrier
                or applied != value.frame.high_watermark):
            raise PrivacyUnavailable()
        proof = RestoreProof(
            authority_id=self.authority_id, high_watermark=self._initial_hwm,
            # The privacy store commits a continuous applied prefix, so a
            # verified DB prefix through B also proves its local replay scan.
            scanned_through=min(applied, self._opening_barrier),
            locally_applied_through=applied,
            complete=applied == value.frame.high_watermark,
            authenticated_current_observation=value.is_fresh(),
            stream_attached=value.frame.session == self._session,
            opening_barrier=self._opening_barrier,
        )
        if not proof.permits_opening(expected_authority_id=self.authority_id):
            raise PrivacyUnavailable()

    async def check_in_transaction(self, session):
        # Both checks matter: authority may become stale/unavailable while the
        # bounded DB statement yields. Never lock privacy_state for reading.
        if self._latest is None or not self._latest.is_fresh():
            raise PrivacyUnavailable()
        row = (await session.execute(text("SELECT authority_id,applied_through FROM analytics.privacy_state WHERE singleton=true"))).one_or_none()
        if row is None or row.authority_id != self.authority_id:
            raise PrivacyUnavailable()
        self._proof(row.applied_through)

    async def is_ready(self):
        try:
            applied = await self._read_cursor()
            if applied is None:
                return False
            self._proof(applied)
            return True
        except Exception:
            return False
