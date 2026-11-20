"""Durable authority journal guarded by a separately persisted monotonic anchor.

A mismatch is deliberately not repaired: it may represent rollback, not a crash.
Both files must be restored only through an independently verified recovery process.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from pymentor_analytics.adapters.privacy_authority.journal import Journal, JournalError
from pymentor_analytics.contracts.privacy_control_v1 import deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence


class ProductionJournal:
    def __init__(self, path: Path, anchor_path: Path, authority_id: str):
        self.path = Path(path).resolve()
        self.anchor_path = Path(anchor_path).resolve()
        if self.path == self.anchor_path or self.path.parent == self.anchor_path.parent:
            raise JournalError("journal and anchor require separate locations")
        self.authority_id = authority_id
        self.journal = Journal(self.path, authority_id)
        if not self.anchor_path.is_file():
            raise JournalError("anchor unavailable")
        with self._locked_anchor() as db:
            self._verify(db)

    @classmethod
    def initialize(cls, path: Path, anchor_path: Path, authority_id: str):
        path, anchor_path = Path(path).resolve(), Path(anchor_path).resolve()
        if path == anchor_path or path.parent == anchor_path.parent:
            raise JournalError("journal and anchor require separate locations")
        if path.exists() or anchor_path.exists() or any(Path(str(p) + suffix).exists() for p in (path, anchor_path) for suffix in ("-journal", "-wal", "-shm")):
            raise JournalError("authority state already exists")
        # Exclusive creation is intentional. Partial initialization is never erased.
        Journal.initialize(path, authority_id)
        try:
            with anchor_path.open("xb"):
                pass
            with closing(sqlite3.connect(str(anchor_path), isolation_level=None)) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("CREATE TABLE anchor (singleton INTEGER PRIMARY KEY CHECK(singleton=1), authority_id TEXT NOT NULL, version INTEGER NOT NULL, high_watermark INTEGER NOT NULL, digest TEXT NOT NULL)")
                db.execute("INSERT INTO anchor VALUES (1, ?, 1, 0, ?)", (authority_id, hashlib.sha256(b"").hexdigest()))
        except (sqlite3.Error, OSError):
            raise JournalError("anchor initialization failed; preserve state") from None
        return cls(path, anchor_path, authority_id)

    def _locked_anchor(self):
        return _AnchorLock(self.anchor_path)

    def _digest(self):
        try:
            with closing(self.journal._connect()) as db:
                db.execute("BEGIN")
                high = db.execute("SELECT high_watermark FROM authority_meta WHERE singleton=1").fetchone()[0]
                digest = hashlib.sha256()
                count = 0
                for row in db.execute("SELECT seq,control_id,producer,control_json,received_at,payload_hash FROM journal ORDER BY seq"):
                    count += 1
                    control = DeletionFence.model_validate_json(row[3])
                    if row[0] != count or str(control.control_id) != row[1] or deletion_hash(row[2], control) != row[5]:
                        raise JournalError("journal content mismatch")
                    digest.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
                if high != count:
                    raise JournalError("journal sequence mismatch")
                db.commit()
                return high, digest.hexdigest()
        except JournalError:
            raise
        except Exception:
            raise JournalError("journal verification failed") from None

    def _verify(self, db):
        try:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise JournalError("anchor corrupt")
            row = db.execute("SELECT authority_id,version,high_watermark,digest FROM anchor WHERE singleton=1").fetchone()
            high, digest = self._digest()
            if row != (self.authority_id, 1, high, digest):
                raise JournalError("anchor mismatch; preserve state for reconciliation")
        except sqlite3.Error:
            raise JournalError("anchor unavailable") from None

    def append(self, producer, control):
        with self._locked_anchor() as db:
            self._verify(db)
            result = self.journal.append(producer, control)
            high, digest = self._digest()
            db.execute("UPDATE anchor SET high_watermark=?,digest=? WHERE singleton=1", (high, digest))
            return result

    def snapshot(self, *, after, through=None, limit=100):
        with self._locked_anchor() as db:
            self._verify(db)
            return self.journal.snapshot(after=after, through=through, limit=limit)


class _AnchorLock:
    def __init__(self, path):
        self.path = path
        self.db = None

    def __enter__(self):
        try:
            uri = "file:" + quote(self.path.as_posix(), safe="/:") + "?mode=rw"
            self.db = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=1000")
            self.db.execute("BEGIN IMMEDIATE")
            return self.db
        except (sqlite3.Error, OSError):
            if self.db is not None:
                self.db.close()
            raise JournalError("anchor unavailable") from None

    def __exit__(self, typ, value, tb):
        try:
            if typ is None:
                self.db.commit()
            else:
                self.db.rollback()
        except sqlite3.Error:
            raise JournalError("anchor commit failed; preserve state") from None
        finally:
            self.db.close()
