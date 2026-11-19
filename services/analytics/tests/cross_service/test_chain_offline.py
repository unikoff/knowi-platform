import asyncio
import os
import subprocess
import pytest
from sqlalchemy import create_engine
from .core_instrumented import Guard
from . import resources as r
from . import chain_checks


def test_proof_vector_requires_matching_cut_epoch_and_no_newer_projection():
    classify = chain_checks.proof_vector_state
    assert classify(3, (3, 0, 0, False)) == "current"
    assert classify(3, None) == "missing_verified"
    assert classify(3, (2, 0, 0, False)) == "stale_verified"
    assert classify(3, (3, 0, 1, False)) == "stale_verified"
    assert classify(3, (3, 0, 0, True)) == "stale_verified"


def test_request_scoped_sql_detector_rejects_legacy_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE user_activity_days (id integer)")
    async def app(scope, receive, send):
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT id FROM user_activity_days")
    guarded = Guard(app)
    async def noop(*args):
        return None
    try:
        asyncio.run(guarded({"type": "http", "headers": []}, noop, noop))
        with pytest.raises(RuntimeError, match="legacy activity SQL"):
            asyncio.run(guarded({"type": "http", "headers": [(b"x-synthetic-no-legacy-sql", b"1")]}, noop, noop))
        asyncio.run(guarded({"type": "http", "headers": []}, noop, noop))
    finally:
        engine.dispose()


def test_browser_practice_fixture_exposes_editor_task_without_judge_data():
    folder, token = r.private_folder()
    env = os.environ.copy()
    env.update(CROSS_SERVICE_PRIVATE_DIR=str(folder), CROSS_SERVICE_BROWSER_PRACTICE="1", CROSS_SERVICE_BROWSER_EXTRA="1", PYTHONPATH=str(r.CORE), PYTHONDONTWRITEBYTECODE="1")
    program = """from pathlib import Path
import os
from pymentor_core.adapters.course_local.runtime import CourseRuntime
from pymentor_core.modules.learning.api.schemas import LessonTaskSummaryResponse
s = CourseRuntime(Path(os.environ['CROSS_SERVICE_PRIVATE_DIR']) / 'courses').snapshot('backend')
extra = CourseRuntime(Path(os.environ['CROSS_SERVICE_PRIVATE_DIR']) / 'courses').snapshot('backend_extra')
assert s.manifest.leader_user_id == 2 and extra.manifest.leader_user_id is None
assert [lesson['id'] for lesson in extra.catalog.LESSONS] == ['extra-lesson-1']
assert extra.catalog.LESSONS[0]['source_file'] != s.catalog.LESSONS[0]['source_file']
task = s.catalog.get_public_lessons('synthetic-track')[0]['tasks'][0]
LessonTaskSummaryResponse.model_validate(task)
assert task['id'] == 'lesson-1-task-01' and len(task['revision']) == 64
assert task['starter_code'].startswith('def solve(number):') and 'tests' not in task
"""
    try:
        python = r.CORE / ".venv/Scripts/python.exe"
        subprocess.run([str(python), "-B", str(r.ROOT / "tests/cross_service/core_fixture.py"), "courses"], cwd=r.CORE, env=env, check=True, timeout=15)
        subprocess.run([str(python), "-B", "-c", program], cwd=r.CORE, env=env, check=True, timeout=15)
    finally:
        r.cleanup_private(folder, token)


def test_browser_pages_fixture_uses_real_p1_projection_and_keeps_extra_course_separate():
    folder, token = r.private_folder()
    env = os.environ.copy()
    env.update(CROSS_SERVICE_PRIVATE_DIR=str(folder), CROSS_SERVICE_BROWSER_PRACTICE="1", CROSS_SERVICE_BROWSER_EXTRA="1",
               CROSS_SERVICE_BROWSER_PAGES="1", PYTHONPATH=str(r.CORE), PYTHONDONTWRITEBYTECODE="1")
    program = """from pathlib import Path
import os
from pymentor_core.adapters.course_local.runtime import CourseRuntime
from pymentor_core.adapters.analytics_catalog.publication import project
root = Path(os.environ['CROSS_SERVICE_PRIVATE_DIR']) / 'courses'
runtime = CourseRuntime(root)
s, extra = runtime.snapshot('backend'), runtime.snapshot('backend_extra')
assert s.manifest.leader_user_id == 2 and extra.manifest.leader_user_id is None
ids = [item['id'] for item in s.catalog.LESSONS]
assert ids == ['lesson-1'] + [f'fixture-page-{index:02d}' for index in range(1,26)]
assert [item['id'] for item in extra.catalog.LESSONS] == ['extra-lesson-1']
assert s.catalog.get_public_lessons('synthetic-track')[0]['tasks'][0]['id'] == 'lesson-1-task-01'
message, digest = project(s)
assert message['kind'] == 'analytics_catalog_snapshot' and message['sequence'] == 1
assert message['catalog_version'].startswith('sha256:') and len(digest) == 64
assert [item['lesson_id'] for item in message['catalog']['lessons']] == ids
"""
    try:
        python = r.CORE / ".venv/Scripts/python.exe"
        subprocess.run([str(python), "-B", str(r.ROOT / "tests/cross_service/core_fixture.py"), "courses"], cwd=r.CORE, env=env, check=True, timeout=15)
        subprocess.run([str(python), "-B", "-c", program], cwd=r.CORE, env=env, check=True, timeout=15)
    finally:
        r.cleanup_private(folder, token)


def test_browser_pages_events_are_valid_distinct_nonoverlapping_and_counted_independently():
    from datetime import datetime, timezone
    from pymentor_analytics.contracts.v2 import BrowserBatch
    from .browser_pages import page_events
    start, end, events = page_events({"catalog_version": "sha256:" + "a" * 64}, datetime(2026, 11, 17, 10, tzinfo=timezone.utc))
    batch = BrowserBatch.model_validate_json(__import__("json").dumps({"events": events}))
    assert len(batch.events) == 25 and len({event.lesson_id for event in batch.events}) == 25
    assert all(left.ended_at <= right.started_at for left, right in zip(batch.events, batch.events[1:]))
    assert start <= batch.events[0].started_at and batch.events[-1].ended_at < end
    assert sum(event.active_ms for event in batch.events if event.section == "theory") == 13000
    assert sum(event.active_ms for event in batch.events if event.section == "practice") == 12000


def test_core_origin_guard_accepts_only_matching_browser_origin():
    program = """import asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymentor_core.app.web import configure_web
app = FastAPI()
configure_web(app)
@app.post('/test')
async def endpoint(): return {'ok': True}
async def main():
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://127.0.0.1:18151') as c:
        for host in ('localhost:18782','127.0.0.2:18782','127.0.0.3:18782'):
            h = {'host':host, 'origin':'http://'+host}
            assert (await c.post('/test',headers=h)).status_code == 200
            h['origin'] = 'http://evil.example'
            assert (await c.post('/test',headers=h)).status_code == 403
asyncio.run(main())
"""
    env = os.environ.copy()
    env.update(PYTHONPATH=str(r.CORE), PYTHONDONTWRITEBYTECODE="1", DATABASE_URL="postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/synthetic")
    subprocess.run([str(r.CORE / ".venv/Scripts/python.exe"), "-B", "-c", program], cwd=r.CORE, env=env, check=True, timeout=15)


def test_profile_outage_requires_two_503s_and_unchanged_quota(monkeypatch):
    requests = []
    class Response:
        status_code = 503
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path, **kwargs):
            requests.append(("GET", path, kwargs))
            return Response()
        def post(self, path, **kwargs):
            requests.append(("POST", path, kwargs))
            return Response()
    monkeypatch.setattr(chain_checks.httpx, "Client", Client)
    monkeypatch.setattr(chain_checks, "login", lambda *args: None)
    monkeypatch.setattr(chain_checks, "quota_snapshot", lambda *args: [("backend", 2026, 11, 2)])
    fixture = {"core_url": "http://127.0.0.1:18151", "users": {"admin": {}, "student": {"id": 3}}}
    chain_checks.assert_profile_query_outage(fixture, {})
    assert [item[0] for item in requests] == ["GET", "POST"]
    assert all(item[2]["headers"]["X-Synthetic-No-Legacy-SQL"] == "1" for item in requests)
    assert requests[1][2]["json"] == {"year": 2026, "month": 11, "add": 1}
