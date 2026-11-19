import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from pymentor_core.adapters.sql import administration, models, role_admin_reads


def test_admin_without_legacy_activity_skips_old_table_queries(synthetic_engine):
    engine = synthetic_engine
    for model in (models.User, models.UserActivityDay, models.UserLessonProgress, models.BookingSlot):
        model.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES (1,'synthetic','synthetic@example.test','hash',false,true)"))
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many: statements.append(statement.lower()))
    with Session(engine) as db:
        result = administration.admin_overview(actor=None, db=db, include_activity=False)
    assert not any("user_activity_days" in statement for statement in statements)
    assert result["active_students_7d"] is None and result["inactive_students_7d"] is None
    assert result["daily_active_students"] == []
    statements.clear()
    with Session(engine) as db:
        result = administration.admin_overview(actor=None, db=db, include_activity=False, include_completions=False)
    assert not any("user_lesson_progress" in statement for statement in statements)
    assert result["lessons_completed_7d"] is None and result["most_active_students"] == []
    statements.clear()
    with Session(engine) as db:
        result = administration.admin_overview(actor=None, db=db)
    assert any("user_activity_days" in statement for statement in statements)
    assert result["active_students_7d"] == 0 and len(result["daily_active_students"]) == 30
    engine.dispose()


def test_platform_analytics_mode_skips_global_activity_count(monkeypatch):
    statements = []
    class Result:
        def __init__(self, value): self.value = value
        def mappings(self): return self
        def one(self): return self.value
        def scalar_one(self): return self.value
    class DB:
        def execute(self, statement, *args):
            sql = str(statement)
            statements.append(sql)
            if "count(*) AS users" in sql:
                return Result({"users": 2, "active_subscribers": 1})
            if "clock_timestamp" in sql:
                return Result(date(2026, 11, 17))
            raise AssertionError("unexpected legacy activity query")
    monkeypatch.setattr(role_admin_reads, "_actor", lambda db, principal: None)
    monkeypatch.setattr(role_admin_reads, "default_runtime", lambda: SimpleNamespace(registry=SimpleNamespace(entries={})))
    monkeypatch.setattr(administration, "admin_overview", lambda principal, db, include_activity=True:
                        {"active_students_7d": None} if not include_activity else (_ for _ in ()).throw(AssertionError()))
    result = role_admin_reads.platform_overview(DB(), object(), include_activity=False)
    assert result["activity_today"]["unique_users"] is None
    assert not any("user_activity_days" in statement for statement in statements)
