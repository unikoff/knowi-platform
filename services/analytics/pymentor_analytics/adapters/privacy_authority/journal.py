"""Explicitly initialized, independent, append-only SQLite fixture journal."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import json
import sqlite3
from urllib.parse import quote

from pydantic import TypeAdapter
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, MAX_SEQUENCE, deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence, Identifier


class JournalConflict(Exception):
    pass


class JournalError(Exception):
    pass


class Journal:
    """One SQLite file unrelated to Analytics PostgreSQL and its backups."""
    def __init__(self, path: Path, authority_id: str):
        self.path = Path(path).resolve()
        self.authority_id = TypeAdapter(Identifier).validate_python(authority_id, strict=True)
        if not self.path.is_file():
            raise JournalError("journal unavailable")
        try:
            with closing(self._connect()) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise JournalError("journal corrupt")
                row = db.execute("SELECT authority_id, version, high_watermark FROM authority_meta WHERE singleton=1").fetchone()
                count, minimum, maximum = db.execute("SELECT count(*),min(seq),max(seq) FROM journal").fetchone()
                if row is None or row[0] != self.authority_id or row[1] != 1 or row[2] != count or (count and (minimum != 1 or maximum != count)):
                    raise JournalError("journal identity or sequence mismatch")
        except (sqlite3.Error, OSError):
            raise JournalError("journal unavailable") from None

    @classmethod
    def initialize(cls, path: Path, authority_id: str) -> "Journal":
        """Explicit first-time creation only; never overwrite or reset a file."""
        authority_id = TypeAdapter(Identifier).validate_python(authority_id, strict=True)
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(str(path), timeout=1)) as db:
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA synchronous=FULL")
                db.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE authority_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                      authority_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version=1), high_watermark INTEGER NOT NULL CHECK(high_watermark>=0));
                    CREATE TABLE journal(seq INTEGER PRIMARY KEY CHECK(seq>=1), control_id TEXT NOT NULL UNIQUE,
                      producer TEXT NOT NULL, control_json TEXT NOT NULL, received_at TEXT NOT NULL, payload_hash TEXT NOT NULL);
                    CREATE TRIGGER journal_no_update BEFORE UPDATE ON journal BEGIN SELECT RAISE(ABORT,'append only'); END;
                    CREATE TRIGGER journal_no_delete BEFORE DELETE ON journal BEGIN SELECT RAISE(ABORT,'append only'); END;
                    COMMIT;
                """)
                db.execute("INSERT INTO authority_meta VALUES(1,?,1,0)", (authority_id,))
                db.commit()
        except (sqlite3.Error, OSError):
            # Preserve the incomplete file as evidence; never auto-reset it.
            raise JournalError("journal initialization failed") from None
        return cls(path, authority_id)

    def _connect(self):
        uri = "file:" + quote(self.path.as_posix(), safe="/:") + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=1000")
            row = db.execute("SELECT authority_id,version FROM authority_meta WHERE singleton=1").fetchone()
            if row != (self.authority_id, 1):
                raise JournalError("journal identity mismatch")
        except BaseException:
            db.close()
            raise
        return db

    def append(self, producer: str, control: DeletionFence) -> tuple[str, int]:
        TypeAdapter(Identifier).validate_python(producer, strict=True)
        if control.epoch > MAX_SEQUENCE:
            raise ValueError("control epoch exceeds storage range")
        digest = deletion_hash(producer, control)
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT seq,payload_hash FROM journal WHERE control_id=?", (str(control.control_id),)).fetchone()
                if existing is not None:
                    if existing[1] != digest:
                        raise JournalConflict()
                    db.commit()
                    return "duplicate", existing[0]
                high = db.execute("SELECT high_watermark FROM authority_meta WHERE singleton=1").fetchone()[0]
                if high >= MAX_SEQUENCE:
                    raise JournalError("journal sequence exhausted")
                seq = high + 1
                received = datetime.now(timezone.utc).isoformat()
                db.execute("INSERT INTO journal VALUES(?,?,?,?,?,?)", (seq, str(control.control_id), producer,
                           control.model_dump_json(), received, digest))
                db.execute("UPDATE authority_meta SET high_watermark=? WHERE singleton=1", (seq,))
                db.commit()
                return "accepted", seq
        except JournalConflict:
            raise
        except (sqlite3.Error, OSError):
            raise JournalError("journal unavailable") from None

    def snapshot(self, *, after: int, through: int | None = None, limit: int = 100) -> tuple[int, tuple[JournalEntry, ...]]:
        if type(after) is not int or not 0 <= after <= MAX_SEQUENCE or type(limit) is not int or not 1 <= limit <= 100:
            raise JournalError("invalid range")
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN")
                high = db.execute("SELECT high_watermark FROM authority_meta WHERE singleton=1").fetchone()[0]
                count, minimum, maximum = db.execute("SELECT count(*),min(seq),max(seq) FROM journal").fetchone()
                if high != count or (count and (minimum != 1 or maximum != count)):
                    raise JournalError("journal sequence gap")
                end = high if through is None else through
                if type(end) is not int or not after <= end <= min(high, MAX_SEQUENCE):
                    raise JournalError("invalid range")
                rows = db.execute("SELECT seq,producer,control_json,received_at,payload_hash FROM journal WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?", (after, end, limit)).fetchall()
                if tuple(row[0] for row in rows) != tuple(range(after + 1, min(end, after + limit) + 1)):
                    raise JournalError("journal sequence gap")
                entries = tuple(JournalEntry.model_validate_json(json.dumps({
                    "seq": row[0], "producer": row[1], "control": json.loads(row[2]),
                    "received_at": row[3], "payload_hash": row[4],
                })) for row in rows)
                db.commit()
                return high, entries
        except JournalError:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError):
            raise JournalError("journal unavailable") from None
