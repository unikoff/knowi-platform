"""Fail-closed a011 restore admission checks, without PostgreSQL."""
import json
from uuid import uuid4

import pytest

from . import resources, restore


class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class FreshTarget:
    def __init__(self, **changes):
        self.values = {"revision": [("a011",)], "sources": 0, "days": 0,
                       "day_backfill": [(None, "scanning", None, None, None)],
                       "bucket_backfill": [(None, "scanning", None, None)],
                       "trigger": [("O", 29, "analytics_test_" + "a" * 32 + "_migrate", True, False, True)],
                       "public_create": False,
                       "role": (False, False, False, False, True, False, False, False, False, False)}
        self.values.update(changes)
        self.statements = []

    def execute(self, statement, params=None):
        text = statement if isinstance(statement, str) else statement.as_string()
        self.statements.append(text)
        if "version_num FROM alembic_version" in text: return Result(self.values["revision"])
        if '"activity_projection_day_sources"' in text: return Result([(self.values["sources"],)])
        if '"activity_projection_days"' in text: return Result([(self.values["days"],)])
        if "FROM analytics.activity_projection_day_backfill" in text: return Result(self.values["day_backfill"])
        if "FROM analytics.activity_projection_backfill" in text: return Result(self.values["bucket_backfill"])
        if "FROM pg_catalog.pg_trigger" in text: return Result(self.values["trigger"])
        if "FROM pg_catalog.pg_namespace" in text: return Result([(self.values["public_create"],)])
        if "SELECT c.relname,pg_get_userbyid(c.relowner)" in text:
            tables = ("activity_projection_day_sources", "activity_projection_days", "activity_projection_day_backfill")
            return Result([(table, TARGET["owner"], self.values.get("public_table") == table,
                            self.values.get("public_column") == table) for table in tables])
        if "has_table_privilege" in text:
            tables, roles = params
            privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
            rows = []
            for table in tables:
                for role in roles:
                    kind = role.removeprefix(TARGET["name"] + "_")
                    allowed = {"SELECT"} if kind in ("processor", "query") else set()
                    if kind == "processor" and table == "activity_projection_days": allowed |= {"INSERT", "UPDATE"}
                    if kind == "privacy" and table != "activity_projection_day_backfill": allowed = {"SELECT", "DELETE"}
                    if self.values.get("missing_privacy_delete") and kind == "privacy": allowed.discard("DELETE")
                    if self.values.get("excess_collector") and kind == "collector": allowed.add("SELECT")
                    rows.append((table, role, *(priv in allowed for priv in privileges)))
            return Result(rows)
        if "has_column_privilege" in text:
            tables, roles = params
            privileges = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
            sources = {"subject_ref", "generation", "state", "claim_id", "owner", "lease_expires_at", "claim_generation",
                       "claim_epoch", "claim_run_id", "attempts", "next_attempt_at", "last_failure"}
            backfill = {"run_id", "phase", "cursor_subject", "cursor_bucket", "cursor_metric"}
            cols = {"activity_projection_day_sources": sources,
                    "activity_projection_days": {"subject_ref", "generation", "run_id"},
                    "activity_projection_day_backfill": backfill}
            rows = []
            for table in tables:
                for role in roles:
                    kind = role.removeprefix(TARGET["name"] + "_")
                    for col in cols[table]:
                        allowed = {"SELECT"} if kind in ("processor", "query") else set()
                        if kind == "privacy" and table != "activity_projection_day_backfill": allowed.add("SELECT")
                        if kind == "transfer_status" and table != "activity_projection_day_backfill" and col == "subject_ref": allowed.add("SELECT")
                        if kind == "processor" and table == "activity_projection_days": allowed |= {"INSERT", "UPDATE"}
                        if kind == "processor" and table == "activity_projection_day_sources" and col not in {"subject_ref", "generation"}: allowed.add("UPDATE")
                        if kind == "processor" and table == "activity_projection_day_backfill" and col != "run_id": allowed.add("UPDATE")
                        if self.values.get("excess_update_generation") and kind == "processor" and table == "activity_projection_day_sources" and col == "generation": allowed.add("UPDATE")
                        rows.append((table, role, col, *(priv in allowed for priv in privileges)))
            return Result(rows)
        if "has_function_privilege" in text:
            roles = params[-1]
            functions = ("activity_projection_bucket_changed", "register_activity_day", "begin_activity_day_backfill", "invalidate_stale_activity_day")
            return Result([(function, role, bool((role.endswith("_processor") and function != "activity_projection_bucket_changed")
                            or (self.values.get("excess_function") and role.endswith("_collector") and function == "register_activity_day")))
                           for function in functions for role in roles])
        if "FROM pg_catalog.pg_proc p WHERE p.oid" in text:
            functions = ("activity_projection_bucket_changed", "register_activity_day", "begin_activity_day_backfill", "invalidate_stale_activity_day")
            return Result([(function, TARGET["owner"], True, bool(self.values.get("public_function") and function == "register_activity_day"), True) for function in functions])
        if "FROM pg_catalog.pg_roles" in text:
            role = self.values["role"]
            if role is not None and self.values.get("collector_membership") and params[-1].endswith("_collector"):
                role = (*role[:-1], True)
            return Result([role] if role is not None else [])
        raise AssertionError("unexpected guard query")


TARGET = {"name": "analytics_test_" + "a" * 32, "owner": "analytics_test_" + "a" * 32 + "_migrate"}


def test_pristine_target_passes_only_read_only_admission():
    conn = FreshTarget()
    restore.verify_fresh_a011_target(conn, TARGET)
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in conn.statements)


@pytest.mark.parametrize("changes", [
    {"revision": [("a010",)]}, {"revision": []}, {"sources": 1}, {"days": 1},
    {"day_backfill": []}, {"day_backfill": [(uuid4(), "scanning", None, None, None)]},
    {"day_backfill": [(None, "complete", None, None, None)]},
    {"bucket_backfill": [(uuid4(), "scanning", None, None)]},
    {"trigger": []}, {"trigger": [("D", 29, TARGET["owner"], True, False, True)]},
    {"trigger": [("O", 29, "other_owner", True, False, True)]},
    {"trigger": [("O", 29, TARGET["owner"], True, True, True)]},
    {"trigger": [("O", 29, TARGET["owner"], True, False, False)]},
    {"public_create": True}, {"role": None},
    {"role": (False, True, False, False, True, False, False, False, False, False)},
    {"missing_privacy_delete": True}, {"excess_collector": True},
    {"public_table": "activity_projection_days"}, {"excess_update_generation": True},
    {"public_column": "activity_projection_day_sources"}, {"public_function": True}, {"excess_function": True},
    {"collector_membership": True},
])
def test_nonempty_partial_wrong_run_disabled_trigger_or_unsafe_roles_stop(changes):
    with pytest.raises(ValueError):
        restore.verify_fresh_a011_target(FreshTarget(**changes), TARGET)


def test_restore_rejects_unknown_or_incomplete_source_run_before_opening_db(monkeypatch):
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: pytest.fail("must not connect"))
    old, new = uuid4(), uuid4()
    snapshot = {"activity_projection_buckets": [], "activity_projection_backfill": [json.dumps({"run_id": str(old), "phase": "scanning"})]}
    for kwargs in ({"old_run_id": None, "new_run_id": new}, {"old_run_id": old, "new_run_id": old},
                   {"old_run_id": old, "new_run_id": new}, {"old_run_id": uuid4(), "new_run_id": new}):
        with pytest.raises(ValueError):
            restore.transplant_fresh(TARGET, snapshot, day_projection=True, **kwargs)


def test_day_snapshot_requires_known_run_before_opening_db(monkeypatch):
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="known run"):
        restore.snapshot(TARGET, uuid4(), browser=True, day_projection=True)


def test_real_role_settings_parse_day_flags_with_one_shared_run():
    from pymentor_analytics.app.config import load_settings, activity_projection_config
    run = uuid4()
    db = "postgresql://user:pass@127.0.0.1:57392/analytics_test_" + "a" * 32
    shared = {"ANALYTICS_ACTIVITY_PROJECTION_RUN_ID": str(run),
              "ANALYTICS_TRUST_MANIFEST_JSON": "{}", "ANALYTICS_PRIVACY_AUTHORITY_JSON": "{}"}
    worker = load_settings("processor", {**shared, "ANALYTICS_PROCESSOR_DATABASE_URL": db,
        "ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED": "true", "ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED": "true"})
    query = load_settings("query", {**shared, "ANALYTICS_QUERY_DATABASE_URL": db,
        "ANALYTICS_QUERY_ACTIVITY_PROJECTION_ENABLED": "true", "ANALYTICS_QUERY_ACTIVITY_DAY_QUERY_ENABLED": "true"})
    assert worker.activity_day_worker_enabled and query.activity_day_query_enabled
    assert activity_projection_config(worker, "processor") == activity_projection_config(query, "query") == run


def test_restored_full_day_requires_new_run_and_exact_observed_totals(monkeypatch):
    from datetime import date
    from . import a011_checks
    run, old, subject = uuid4(), uuid4(), uuid4()
    day = date(2026, 11, 16)
    monkeypatch.setattr(a011_checks, "_grant_token", lambda *a, **kw: "synthetic-token")
    monkeypatch.setattr(a011_checks, "day_row", lambda *a: ("ready", 3, 3, old, 0, 1))
    with pytest.raises(AssertionError):
        a011_checks.assert_restored_day("http://127.0.0.1:18156", {}, None, run, subject, day)
    monkeypatch.setattr(a011_checks, "day_row", lambda *a: ("ready", 3, 3, run, 0, 1))
    class Response:
        status_code = 200
        def json(self):
            return {"totals": {"unique_visitors": 1, "active_ms": 15000, "theory_ms": 10000, "practice_ms": 5000}}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path, **kwargs):
            assert path == "/api/analytics/reports/activity"
            assert kwargs["params"]["from_time"] == "2026-11-15T21:00:00+00:00"
            assert kwargs["params"]["to_time"] == "2026-11-16T21:00:00+00:00"
            return Response()
    monkeypatch.setattr(a011_checks.httpx, "Client", Client)
    a011_checks.assert_restored_day("http://127.0.0.1:18156", {}, None, run, subject, day)


def test_day_ingress_events_use_unchanged_b2_contract_and_nonoverlapping_times():
    from datetime import datetime, timezone
    from pymentor_analytics.contracts.v2 import BrowserBatch
    from .a011_checks import day_event
    start = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
    fixture = {"catalog_version": "sha256:" + "a" * 64}
    first = day_event(fixture, start, 0, 10, "theory")
    late = day_event(fixture, start, 20, 5, "practice")
    events = BrowserBatch.model_validate_json(json.dumps({"events": [first, late]})).events
    assert events[0].ended_at < events[1].started_at
    assert [event.active_ms for event in events] == [10000, 5000]
    assert events[0].event_id != events[1].event_id


def test_install_revisions_are_explicit_and_default_a010_unchanged(monkeypatch):
    from pymentor_analytics.adapters.postgres import migrate
    calls = []
    monkeypatch.setattr(migrate, "upgrade", lambda settings, *, target_revision: calls.append(target_revision))
    urls = {"migrate": "postgresql://user:pass@127.0.0.1:57392/analytics_test_" + "a" * 32}
    resources.install_analytics(urls, full_chain=True, activity_projection=True)
    resources.install_analytics(urls, full_chain=True, activity_projection=True, day_projection=True)
    assert calls == ["a010", "a011"]
    with pytest.raises(ValueError):
        resources.install_analytics(urls, full_chain=True, day_projection=True)
