"""The opt-in wire contract is self-only and leaves old clients unchanged."""
import asyncio

from test_activity_http import make_app, request, PARAMS, OTHER

ALIGNED = {**PARAMS, "from_time": "2025-12-31T21:00:00Z", "to_time": "2026-01-01T21:00:00Z"}


def test_opt_in_fallback_is_unknown_and_old_wire_unchanged():
    app, token = make_app()
    normal = asyncio.run(request(app, token, ALIGNED))
    opted = asyncio.run(request(app, token, {**ALIGNED, "history_revisions": "true"}))
    assert normal.status_code == opted.status_code == 200
    assert "history_revisions" not in normal.json()
    body = opted.json()
    revisions = body.pop("history_revisions")
    assert body.pop("history_generation") is None
    assert "history_generation" not in normal.json()
    assert len(revisions) == 28 and all(item["token"] is None for item in revisions)
    assert body == normal.json() and opted.headers["cache-control"] == "no-store"


def test_opt_in_rejects_other_scopes_courses_and_partial_days():
    app, token = make_app()
    opt = {**ALIGNED, "history_revisions": "true"}
    assert asyncio.run(request(app, token, {**opt, "course_id": "other"})).status_code == 403
    assert asyncio.run(request(app, token, {**opt, "subject_ref": str(OTHER)})).status_code == 400
    assert asyncio.run(request(app, token, {**opt, "lesson_id": "lesson-a"})).status_code == 400
    assert asyncio.run(request(app, token, {**PARAMS, "history_revisions": "true"})).status_code == 400
    leader, leader_token = make_app(role="leader", subject=OTHER, roster=(OTHER,))
    assert asyncio.run(request(leader, leader_token, opt)).status_code == 403


def test_pending_report_never_returns_cached_or_zero_values():
    from pymentor_analytics.adapters.postgres.activity_projection_reports import ProjectionPending

    class Pending:
        supports_history_revisions = True
        async def read_activity(self, grant, query, *, include_history_revisions=False):
            raise ProjectionPending()

    app, token = make_app()
    app.state.activity_reports = Pending()
    result = asyncio.run(request(app, token, {**ALIGNED, "history_revisions": "true"}))
    assert result.status_code == 503 and "history_revisions" not in result.json()
