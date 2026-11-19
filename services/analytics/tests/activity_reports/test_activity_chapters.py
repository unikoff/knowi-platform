import asyncio
import httpx
import pytest
from test_activity_http import make_app, PARAMS, PATH

BODY = {**PARAMS, "chapters": [{"chapter_id": "chapter 1", "lesson_ids": [f"lesson-{i}" for i in range(25)]}, {"chapter_id": "empty", "lesson_ids": []}]}
async def post(app, token, body=BODY):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(PATH + "/chapters", json=body, headers={"Authorization": f"Bearer {token}"})
class Snapshot:
    def __init__(self, quality): self.quality = quality
    async def read_activity(self, grant, query, *, include_breakdown=False):
        from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope
        resolve_scope(grant, query)
        assert include_breakdown and query.subject_ref is None and query.lesson_id is None
        def row(level, lesson, course="backend"):
            return {"level": level, "course_id": course, "lesson_id": lesson, "unique_visitors": 1, "active_ms": 3, "theory_ms": 3, "practice_ms": 0}
        return {"quality": self.quality, "as_of": None, "lag_seconds": None, "course_id": "backend", "totals": {"unique_visitors": 0, "active_ms": 0, "theory_ms": 0, "practice_ms": 0}, "daily": [], "breakdown": [row("course", None), *[row("lesson", f"lesson-{i}") for i in range(25)], row("lesson", "lesson-0", "other")]}
@pytest.mark.parametrize("quality,zero", [("unknown", None), ("partial", None), ("complete", 0)])
def test_chapters_snapshot_and_coverage(quality, zero):
    app, token = make_app(); app.state.activity_reports = Snapshot(quality)
    result = asyncio.run(post(app, token))
    assert result.status_code == 200, result.text
    assert result.json()["chapters"] == [{"chapter_id": "chapter 1", "active_ms": 75, "theory_ms": 75, "practice_ms": zero}, {"chapter_id": "empty", "active_ms": zero, "theory_ms": zero, "practice_ms": zero}]
def test_scope_and_validation():
    app, token = make_app(); app.state.activity_reports = Snapshot("unknown")
    assert asyncio.run(post(app, token, {**BODY, "course_id": "other"})).status_code == 403
    for role in ("leader", "admin"):
        other, other_token = make_app(role=role, courses=() if role == 'admin' else ('backend',), global_read=role == 'admin')
        assert asyncio.run(post(other, other_token)).status_code == 403
    assert asyncio.run(post(app, token, {**BODY, "subject_ref": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})).status_code == 422
    for chapters in ([{"chapter_id":"a", "lesson_ids":["x"]}, {"chapter_id":"a", "lesson_ids":[]}], [{"chapter_id":"a", "lesson_ids":["x"]}, {"chapter_id":"b", "lesson_ids":["x"]}], [{"chapter_id":"a", "lesson_ids":[f"l-{i}" for i in range(2001)]}], [{"chapter_id":str(i), "lesson_ids":[]} for i in range(101)]):
        assert asyncio.run(post(app, token, {**BODY, "chapters":chapters})).status_code == 422
    assert asyncio.run(post(app, token, {**BODY, "from_time":"2026-01-01T03:00:00+03:00"})).status_code == 422
    assert asyncio.run(post(app, token, {**BODY, "chapters":[]})).json()["chapters"] == []
    app.state.trust_manifest_validated = False
    assert asyncio.run(post(app, token)).status_code == 503
