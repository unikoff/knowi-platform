import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import metadata, revisions, write_gate
from pymentor_core.modules.analytics_delivery import source_watermark

REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_watermark_reads_real_roster_revision_and_rejects_missing_lifecycle(synthetic_engine, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "1")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "1")
    monkeypatch.setattr(source_watermark, "authorize_grant", lambda *args, **kwargs: {
        "role": "admin", "subject": REF, "scope": "report:read", "courses": [], "subjects": [], "global_read": True})
    engine = synthetic_engine
    models.User.__table__.create(engine)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE account_lifecycles(user_id INTEGER PRIMARY KEY,lifecycle_id VARCHAR(36) UNIQUE NOT NULL)"))
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES(1,'synthetic','s@example.test','hash',true,true)"))
        conn.execute(text("INSERT INTO account_lifecycles VALUES(1,:ref)"), {"ref": REF})
        conn.execute(write_gate.insert().values(singleton=1, mode="mirror"))
        conn.execute(revisions.insert().values(subject_ref=REF, revision=3))
    with Session(engine) as db:
        _, members = source_watermark.capture(db, SimpleNamespace(), "admin")
        assert members == [{"subject_ref": REF, "revision": 3, "student": False}]
        db.execute(text("DELETE FROM account_lifecycles"))
        with pytest.raises(ValueError, match="roster"):
            source_watermark.capture(db, SimpleNamespace(), "admin")
    engine.dispose()
