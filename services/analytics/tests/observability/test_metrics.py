import asyncio
import json

from fastapi.testclient import TestClient

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.app.telemetry import IntakeTelemetryMiddleware, Telemetry
from pymentor_analytics.contracts.quality_v1 import DiagnosticSnapshot, JobCount

URL = "postgresql://synthetic@127.0.0.1:57392/analytics_test"


def test_ack_counters_are_bounded_and_do_not_retain_identity_or_bad_labels():
    counters = Telemetry()
    counters.response(200, json.dumps({"items": [{"event_id": "sensitive-id", "status": key} for key in ("accepted", "duplicate", "conflict", "rejected", "pending")]}).encode())
    counters.response(401, b"sensitive body")
    counters.response(503, b"outcome unknown")
    counters.response(200, b'{"items":[{"status":"untrusted-label"}]}')
    text = counters.render("collector", False)
    for key in ("accepted", "duplicate", "conflict", "rejected", "pending"):
        assert f'analytics_collector_ack_events_total{{outcome="{key}"}} 1' in text
    assert 'analytics_collector_requests_total{outcome="unauthorized"} 1' in text
    assert 'analytics_collector_requests_total{outcome="unavailable"} 1' in text
    assert "analytics_instrumentation_errors_total 1" in text
    assert "sensitive" not in text and "untrusted-label" not in text


def test_streamed_or_oversized_ack_never_grows_process_state():
    async def run():
        counters = Telemetry()
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            for _ in range(100):
                await send({"type": "http.response.body", "body": b"x"*4096, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        sent = 0
        async def send(message):
            nonlocal sent
            sent += 1
        await IntakeTelemetryMiddleware(app, telemetry=counters)({"type": "http", "method": "POST", "path": "/api/analytics/events/batch"}, None, send)
        assert sent == 102
        assert counters.instrumentation_errors == 1 and sum(counters.events.values()) == 0
    asyncio.run(run())


def test_private_processor_metrics_work_while_privacy_is_closed():
    snapshot = DiagnosticSnapshot(3, (JobCount("synthetic.activity", 1, "pending", 2),), 0, 0, 8, 1, 0, 1, 1024, 2, None)
    class Diagnostics:
        async def read(self): return snapshot
    with TestClient(create_app("processor", Settings(database_url=URL), diagnostics=Diagnostics())) as client:
        result = client.get("/metrics")
        assert result.status_code == 200
        assert "analytics_diagnostics_available 1" in result.text
        assert "analytics_stored_facts 3" in result.text
        assert 'analytics_privacy_ready{role="processor"} 0' in result.text
        assert "analytics_privacy_applied_through_known 0" in result.text
        assert "/metrics" not in client.app.openapi()["paths"]


def test_diagnostic_failure_or_arbitrary_labels_do_not_leak_in_response():
    for value in (None, DiagnosticSnapshot(3, (JobCount('user@example.com', 1, "pending", 2),), 0, 0, 8, 1, 0, 1, 1024, 2, None)):
        class Diagnostics:
            async def read(self):
                if value is None: raise RuntimeError("private-credential")
                return value
        with TestClient(create_app("processor", Settings(database_url=URL), diagnostics=Diagnostics())) as client:
            result = client.get("/metrics")
            assert result.status_code == 200 and "analytics_diagnostics_available 0" in result.text
            assert "analytics_database_waiting_locks_known 0" in result.text
            assert "private-credential" not in result.text and "user@example.com" not in result.text


def test_pool_metrics_are_bounded_and_unsupported_values_are_unknown():
    counters = Telemetry()
    text = counters.render("collector", False, pool=(5, 2, 3, 15, 0))
    assert 'analytics_database_pool_checked_out{role="collector"} 3' in text
    assert 'analytics_database_pool_max{role="collector"} 15' in text
    text = counters.render("collector", False, pool=(5, 2, 3, None, -1))
    assert 'analytics_database_pool_max_known{role="collector"} 0' in text
    assert 'analytics_database_pool_overflow_known{role="collector"} 0' in text
    assert 'analytics_database_pool_overflow{role=' not in text


def test_pool_snapshot_handles_unallocated_overflow_and_unsupported_pool():
    from pymentor_analytics.adapters.postgres.database import Database
    db = Database(Settings(database_url=URL, pool_size=2, pool_overflow=0))
    try:
        assert db.pool_snapshot() == (2, 0, 0, 2, 0)
        original_pool = db.engine.pool
        db.engine.pool = object()
        assert db.pool_snapshot() is None
        db.engine.pool = original_pool
    finally:
        asyncio.run(db.close())


def test_waiting_locks_are_optional_and_validated():
    snapshot = DiagnosticSnapshot(3, (), 0, 0, None, 0, 0, 0, 0, 0, None, 4)
    text = Telemetry().render("processor", False, snapshot)
    assert "analytics_database_waiting_locks_known 1" in text
    assert "analytics_database_waiting_locks 4" in text
    snapshot = DiagnosticSnapshot(3, (), 0, 0, None, 0, 0, 0, 0, 0, None)
    text = Telemetry().render("processor", False, snapshot)
    assert "analytics_database_waiting_locks_known 0" in text
    assert "analytics_database_waiting_locks 0" not in text


def test_lock_query_filters_waiting_sessions_to_current_database():
    from pathlib import Path
    source = (Path(__file__).parents[2] / "pymentor_analytics/adapters/postgres/quality.py").read_text()
    assert "JOIN pg_stat_activity AS activity ON activity.pid=locks.pid" in source
    assert "activity.datname=current_database() AND NOT locks.granted" in source
