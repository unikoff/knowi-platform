"""Calendar request validation and grant boundaries."""
import asyncio
import httpx
from test_activity_http import make_app

PATH = "/api/analytics/reports/activity/calendar"
BODY = {"course_id": "backend", "from_time": "2026-01-01T21:00:00Z", "to_time": "2026-01-03T21:00:00Z",
        "known_generation": None, "known_revisions": []}


def post(app, token, body):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(PATH, json=body, headers={"Authorization": f"Bearer {token}"})
    return asyncio.run(send())


def test_calendar_rejects_unauthorized_and_unsupported_storage():
    app, token = make_app()
    assert post(app, token, BODY).status_code == 501
    assert post(app, "bad", BODY).status_code == 401
    assert post(app, token, {**BODY, "course_id": "other"}).status_code == 403
    assert post(app, token, {**BODY, "from_time": "2026-01-01T00:00:00Z"}).status_code == 400
    assert post(app, token, {**BODY, "known_generation": "not-a-hash"}).status_code == 422
    assert post(app, token, {**BODY, "known_revisions": [{"date":"2026-01-02", "token":"a"*64}]*2}).status_code == 422
    leader, leader_token = make_app(role="leader")
    assert post(leader, leader_token, BODY).status_code == 403


def test_query_factory_exposes_calendar_contract():
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.factory import create_app
    app = create_app("query", Settings(database_url="postgresql://synthetic@localhost:57392/analytics_test"))
    operation = app.openapi()["paths"][PATH]["post"]
    assert operation["operationId"] == "getActivityCalendar"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/CalendarResponse")
    assert operation["security"] == [{"AnalyticsGrant": []}]
