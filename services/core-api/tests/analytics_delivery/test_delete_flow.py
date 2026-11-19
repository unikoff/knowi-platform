import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from pymentor_core.adapters.sql import models, administration, role_admin_commands, role_admin_cutover
from pymentor_core.adapters.sql.analytics_delivery import metadata, outbox
from pymentor_core.adapters.sql.analytics_delivery_ops import worker
from pymentor_core.modules.analytics_delivery.relay import Receipt
from pymentor_core.modules.administration.api import router as admin_router
from pymentor_core.shared.errors import ApplicationError

OLD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NEW = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def database(synthetic_engine, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "1")
    engine = synthetic_engine
    for model in (models.User, models.UserActivityDay, models.UserLessonProgress, models.UserTaskProgress,
                  models.Session, models.BookingSlot, models.BookingQuota, models.BookingNotificationOutbox):
        model.__table__.create(engine)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE account_lifecycles(user_id INTEGER PRIMARY KEY, lifecycle_id VARCHAR(36) UNIQUE NOT NULL)"))
        conn.execute(text("CREATE TABLE account_deletion_events(user_id INTEGER NOT NULL, lifecycle_id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE booking_notification_subjects(outbox_id INTEGER, lifecycle_id VARCHAR(36))"))
        # Model the c426 journal and lifecycle removal at the database boundary.
        conn.execute(text("""CREATE FUNCTION pymentor_capture_account_deletion(p_user_id integer) RETURNS void LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO account_deletion_events SELECT p_user_id,lifecycle_id FROM account_lifecycles WHERE user_id=p_user_id
                ON CONFLICT (lifecycle_id) DO NOTHING;
            END $$"""))
        conn.execute(text("""CREATE FUNCTION synthetic_capture_fn() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pymentor_capture_account_deletion(OLD.id);
                DELETE FROM account_lifecycles WHERE user_id=OLD.id;
                RETURN OLD;
            END $$"""))
        conn.execute(text("""CREATE TRIGGER synthetic_capture BEFORE DELETE ON users
            FOR EACH ROW EXECUTE FUNCTION synthetic_capture_fn()"""))
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES (1,'admin','a@example.test','hash',true,true),(2,'student','b@example.test','hash',false,true)"))
        conn.execute(text("INSERT INTO account_lifecycles VALUES (2,:ref)"), {"ref": OLD})
    monkeypatch.setattr(role_admin_cutover, "lock_mode", lambda db: None)
    monkeypatch.setattr(role_admin_commands, "_lock_users", lambda db, ids: {})
    monkeypatch.setattr(role_admin_commands, "_authority", lambda db, actor, users, course: None)
    monkeypatch.setattr(worker, "retry_delay", lambda *args, **kwargs: 0)
    yield engine, sessionmaker(engine)
    engine.dispose()


def test_deleted_generation_pending_then_verified_purge_and_reused_id_safe(database):
    engine, sessions = database
    actor = SimpleNamespace(id=1)
    with sessions() as db:
        assert administration.delete_user(2, actor, db, expected_account_ref=OLD) == {"_analytics_deletion_state": "pending"}
    with sessions() as db:
        assert db.get(models.User, 2) is None
        assert administration.delete_user(2, actor, db, expected_account_ref=OLD) == {"_analytics_deletion_state": "pending"}
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES (2,'new','new@example.test','hash',false,true)"))
        conn.execute(text("INSERT INTO account_lifecycles VALUES (2,:ref)"), {"ref": NEW})
    with sessions() as db:
        assert administration.delete_user(2, actor, db, expected_account_ref=OLD) == {"_analytics_deletion_state": "pending"}
        assert db.get(models.User, 2).username == "new"
        with pytest.raises(ApplicationError) as error:
            administration.delete_user(2, actor, db)
        assert error.value.status_code == 400

    class Authority:
        async def append(self, event, created_at, timeout_seconds): return 1
    class Receiver:
        async def deletion_status(self, event, deadline): return Receipt.ACCEPTED
    asyncio.run(worker.run_once(sessions, Receiver(), authority=Authority()))
    with sessions() as db:
        assert db.execute(select(outbox.c.status)).scalar_one() == "delivered"
        assert administration.delete_user(2, actor, db, expected_account_ref=OLD) == {"_analytics_deletion_state": "complete"}
        assert db.get(models.User, 2).username == "new"
        assert db.scalar(text("SELECT lifecycle_id FROM account_lifecycles WHERE user_id=2")) == NEW


def test_delete_http_distinguishes_pending_completed_and_legacy(monkeypatch):
    app = FastAPI()
    app.include_router(admin_router.admin_router, prefix="/api/admin")
    result = {}
    async def fake_admin_sql(request, operation): return result.copy()
    monkeypatch.setattr(admin_router, "_admin_sql", fake_admin_sql)
    with TestClient(app) as client:
        result.update(_analytics_deletion_state="pending")
        response = client.delete("/api/admin/users/2", headers={"X-Account-Ref": OLD})
        assert response.status_code == 202 and response.json() == {"status": "pending"}
        assert response.headers["retry-after"] == "2"
        result["_analytics_deletion_state"] = "complete"
        response = client.delete("/api/admin/users/2", headers={"X-Account-Ref": OLD})
        assert response.status_code == 204 and response.content == b""
        result.clear(); result.update(deleted=2)
        response = client.delete("/api/admin/users/2")
        assert response.status_code == 200 and response.json() == {"deleted": 2}


def test_deletion_status_is_authorized_and_missing_outbox_fails_closed(database, monkeypatch):
    engine, sessions = database
    actor = SimpleNamespace(id=1)
    with sessions() as db:
        administration.delete_user(2, actor, db, expected_account_ref=OLD)
    def deny(*args):
        raise ApplicationError(status_code=403, detail="forbidden")
    monkeypatch.setattr(role_admin_commands, "_authority", deny)
    with sessions() as db:
        with pytest.raises(ApplicationError) as forbidden:
            administration.delete_user(2, actor, db, expected_account_ref=OLD)
        assert forbidden.value.status_code == 403
    monkeypatch.setattr(role_admin_commands, "_authority", lambda *args: None)
    with engine.begin() as conn:
        conn.execute(outbox.delete())
    with sessions() as db:
        with pytest.raises(ApplicationError) as unavailable:
            administration.delete_user(2, actor, db, expected_account_ref=OLD)
        assert unavailable.value.status_code == 503
