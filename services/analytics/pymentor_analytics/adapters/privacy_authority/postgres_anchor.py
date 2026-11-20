"""Independent PostgreSQL anchor, provisioned out of band, with verified TLS."""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from pymentor_analytics.adapters.privacy_authority.journal import Journal, JournalError
from pymentor_analytics.adapters.privacy_authority.production_journal import ProductionJournal


class PostgresAnchorJournal(ProductionJournal):
    """Anchor row must exist; no DDL, automatic seed, or mismatch recovery."""
    def __init__(self, path, authority_id, *, dsn, ca_file, host, database, user):
        self.path = Path(path).resolve()
        self.authority_id = authority_id
        self._connection = self._validate_connection(dsn, ca_file, host, database, user)
        self.journal = Journal(self.path, authority_id)
        with self._locked_anchor() as db:
            self._verify(db)

    @staticmethod
    def _validate_connection(dsn, ca_file, host, database, user):
        try:
            parts = conninfo_to_dict(dsn)
            allowed = {"host", "port", "dbname", "user", "password"}
            if set(parts) - allowed or not {"host", "dbname", "user", "password"} <= set(parts):
                raise ValueError("invalid anchor DSN")
            if (parts["host"] != host or parts["dbname"] != database or parts["user"] != user
                    or not all(isinstance(v, str) and v for v in (host, database, user, parts["password"]))
                    or any(c in host for c in (",", "/", " "))
                    or not Path(ca_file).is_absolute() or not Path(ca_file).is_file()):
                raise ValueError("anchor identity mismatch")
            port = parts.get("port", "5432")
            if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError("invalid anchor port")
            return {**parts, "port": port, "sslmode": "verify-full", "sslrootcert": str(ca_file),
                    "gssencmode": "disable", "connect_timeout": 2}
        except Exception:
            raise ValueError("invalid PostgreSQL anchor configuration") from None

    @classmethod
    def initialize(cls, path, authority_id, *, dsn, ca_file, host, database, user):
        instance = cls.__new__(cls)
        instance.path = Path(path).resolve()
        instance.authority_id = authority_id
        instance._connection = cls._validate_connection(dsn, ca_file, host, database, user)
        with instance._locked_anchor() as db:
            row = instance._anchor_row(db)
            if row != (authority_id, 1, 0, hashlib.sha256(b"").hexdigest()):
                raise JournalError("anchor not provisioned empty")
            if instance.path.exists() or any(Path(str(instance.path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
                raise JournalError("journal already exists")
            instance.journal = Journal.initialize(instance.path, authority_id)
            instance._verify(db)
        return instance

    def _locked_anchor(self):
        return _PostgresLock(self._connection)

    def _anchor_row(self, db):
        row = db.execute("SELECT authority_id, version, high_watermark, digest FROM public.privacy_authority_anchor WHERE authority_id = %s FOR UPDATE", (self.authority_id,)).fetchone()
        return row

    def _verify(self, db):
        try:
            row = self._anchor_row(db)
            high, digest = self._digest()
            if row != (self.authority_id, 1, high, digest):
                raise JournalError("anchor mismatch; preserve state for reconciliation")
        except psycopg.Error:
            raise JournalError("anchor unavailable") from None

    def append(self, producer, control):
        try:
            with self._locked_anchor() as db:
                self._verify(db)
                previous = self._anchor_row(db)
                result = self.journal.append(producer, control)
                high, digest = self._digest()
                if high not in (previous[2], previous[2] + 1) or (high == previous[2] and digest != previous[3]):
                    raise JournalError("non-monotonic checkpoint")
                changed = db.execute("UPDATE public.privacy_authority_anchor SET high_watermark=%s,digest=%s WHERE authority_id=%s AND high_watermark=%s AND digest=%s", (high, digest, self.authority_id, previous[2], previous[3])).rowcount
                if changed != 1:
                    raise JournalError("anchor update failed")
                return result
        except psycopg.Error:
            raise JournalError("anchor unavailable; preserve state") from None


class _PostgresLock:
    def __init__(self, config):
        self.config = config
        self.db = None

    def __enter__(self):
        try:
            self.db = psycopg.connect(**self.config)
            if not self.db.pgconn.ssl_in_use:
                raise JournalError("anchor TLS unavailable")
            self.db.execute("SET LOCAL synchronous_commit = 'on'")
            self.db.execute("SET LOCAL lock_timeout = '1s'")
            self.db.execute("SET LOCAL statement_timeout = '2s'")
            identity = self.db.execute("SELECT current_database(), current_user, pg_is_in_recovery()").fetchone()
            if identity != (self.config["dbname"], self.config["user"], False):
                raise JournalError("anchor identity mismatch")
            return self.db
        except Exception:
            if self.db is not None:
                self.db.close()
            raise JournalError("anchor unavailable") from None

    def __exit__(self, typ, value, tb):
        try:
            if typ is None:
                self.db.commit()
            else:
                self.db.rollback()
        except Exception:
            raise JournalError("anchor commit unresolved; preserve state") from None
        finally:
            self.db.close()
