import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from pymentor_analytics.adapters.postgres.activity_projection_reports import ProjectionPending
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope
from test_activity_http import make_app, OTHER, PARAMS, PATH, request, USER


def times(theory=0, practice=0):
    return {"active_ms": theory + practice, "theory_ms": theory, "practice_ms": practice}


def row(lesson_id, theory=0, practice=0, *, course="backend"):
    return {
        "level": "course" if lesson_id is None else "lesson",
        "course_id": course,
        "lesson_id": lesson_id,
        "unique_visitors": 1,
        **times(theory, practice),
    }


class Snapshot:
    def __init__(self, rows=(), *, overrides=None, failure=None, lifetime_actor=USER, lifetime_target=None):
        self.rows = list(rows)
        self.overrides = overrides or {}
        self.failure = failure
        self.calls = 0
        self.lifetime_actor = lifetime_actor
        self.lifetime_target = lifetime_target

    async def read_activity(self, grant, query, *, include_breakdown=False):
        self.calls += 1
        scope = resolve_scope(grant, query)
        assert scope.subjects == (USER,) and scope.courses == ("backend",)
        assert include_breakdown and query.subject_ref is None and query.lesson_id is None
        assert query.timezone == "Europe/Moscow"
        return self.snapshot(scope.course_id)

    async def read_lifetime_activity(self, grant, query):
        self.calls += 1
        scope = resolve_scope(grant, query)
        assert scope.actor == self.lifetime_actor and scope.courses == ("backend",)
        assert scope.subjects == (self.lifetime_target or self.lifetime_actor,)
        assert scope.selected_subject == query.subject_ref == self.lifetime_target and query.lesson_id is None
        assert query.to_time - query.from_time > timedelta(days=31)
        snapshot = self.snapshot(scope.course_id)
        return {key: value for key, value in snapshot.items() if key not in {"daily", "lag_seconds"}}

    def snapshot(self, course_id):
        if self.failure:
            raise self.failure()
        totals = {"unique_visitors": 0, **times()}
        daily = []
        if self.rows:
            course_row = next(r for r in self.rows if r["level"] == "course")
            totals = {key: course_row[key] for key in totals}
            daily = [{"date": date(2026, 1, 1), **totals}]
        return {
            "course_id": course_id,
            "quality": "unknown",
            "as_of": datetime(2026, 1, 3, tzinfo=timezone.utc) if daily else None,
            "lag_seconds": None,
            "totals": totals,
            "daily": daily,
            "breakdown": self.rows,
            **self.overrides,
        }


BODY = {
    **PARAMS,
    "chapters": [{"chapter_id": "Глава 1", "lesson_ids": ["lesson-0", "lesson-1"]}],
}
LIFETIME_SUFFIX = "/outline/lifetime"
LIFETIME_BODY = {"course_id": BODY["course_id"], "chapters": BODY["chapters"]}
STUDENT_LIFETIME_SUFFIX = LIFETIME_SUFFIX + "/student"
STUDENT_LIFETIME_BODY = {**LIFETIME_BODY, "subject_ref": str(USER)}


async def post(app, token, body=BODY, *, suffix="/outline"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(PATH + suffix, json=body, headers={"Authorization": f"Bearer {token}"})


def test_outline_returns_lesson_time_beyond_page_and_preserves_unmapped_rows_in_one_snapshot():
    app, token = make_app()
    lesson_ids = [f"lesson-{index:03}" for index in range(125)]
    later_ids = lesson_ids[115:]
    store = Snapshot([
        row(None, 128, 251),
        *(row(lesson_id, 1, 2) for lesson_id in reversed(later_ids)),
        *(row(lesson_id, 1, 2) for lesson_id in reversed(lesson_ids[1:115])),
        row("archived-z", 0, 3),
        row("archived-a", 4, 0),
        row("lesson-000", 900, 0, course="other"),
        row("from-other", 900, 0, course="other"),
    ])
    # The aggregator returns the entire requested selection in catalog order.
    app.state.activity_reports = store
    selection = [
        {"chapter_id": "Глава 1", "lesson_ids": lesson_ids[:-1]},
        {"chapter_id": "Глава 2", "lesson_ids": lesson_ids[-1:]},
    ]
    body = {**PARAMS, "chapters": selection}
    result = asyncio.run(post(app, token, body))
    assert result.status_code == 200, result.text
    assert store.calls == 1
    data = result.json()
    assert {name: data[name] for name in PARAMS} == PARAMS
    assert data["status"] == "ok" and data["quality"] == "unknown"
    assert data["as_of"] == "2026-01-03T00:00:00Z"
    assert [item["lesson_id"] for item in data["lessons"]] == lesson_ids
    assert data["lessons"][0] == {"lesson_id": "lesson-000", **times()}
    assert data["lessons"][124] == {"lesson_id": "lesson-124", **times(1, 2)}
    assert data["unmapped_lessons"] == [
        {"lesson_id": "archived-a", **times(4, 0)},
        {"lesson_id": "archived-z", **times(0, 3)},
    ]
    assert data["chapters"] == [
        {"chapter_id": "Глава 1", **times(123, 246)},
        {"chapter_id": "Глава 2", **times(1, 2)},
    ]
    old = asyncio.run(post(app, token, body, suffix="/chapters"))
    assert old.status_code == 200
    assert old.json() == {k: v for k, v in data.items() if k not in {"lessons", "unmapped_lessons"}}


@pytest.mark.parametrize("rows", [[], [row(None)]], ids=["no-facts", "course-opened-without-duration"])
def test_outline_materializes_lesson_zeros_without_claiming_complete_coverage(rows):
    app, token = make_app()
    app.state.activity_reports = Snapshot(rows)
    result = asyncio.run(post(app, token))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "ok" and data["quality"] == "unknown"
    assert data["chapters"] == [{"chapter_id": "Глава 1", "active_ms": None, "theory_ms": None, "practice_ms": None}]
    assert data["lessons"] == [{"lesson_id": lesson_id, **times()} for lesson_id in BODY["chapters"][0]["lesson_ids"]]
    assert data["unmapped_lessons"] == []


@pytest.mark.parametrize("rows", [
    [row(None, 8, 3), row("lesson-0", 6, 3)],
    [row(None, 8, 3), row("lesson-0", 9, 2)],
], ids=["truncated-duration", "incorrect-sectional-split"])
def test_outline_rejects_mismatched_lesson_breakdown_and_preserves_legacy_report(rows):
    app, token = make_app()
    app.state.activity_reports = Snapshot(rows)
    result = asyncio.run(post(app, token))
    assert result.status_code == 503, result.text
    assert result.json()["code"] == "unavailable"
    assert "lessons" not in result.json()
    old = asyncio.run(post(app, token, suffix="/chapters"))
    assert old.status_code == 200
    assert old.json()["chapters"] == [{"chapter_id": "Глава 1", **times(rows[1]["theory_ms"], rows[1]["practice_ms"])}]


def test_outline_rejects_invalid_grant_access_and_client_subject_selectors():
    app, token = make_app()
    store = Snapshot()
    app.state.activity_reports = store
    assert asyncio.run(post(app, "wrong-token")).status_code == 401
    assert asyncio.run(post(app, token, {**BODY, "course_id": "other"})).status_code == 403
    assert asyncio.run(post(app, token, {**BODY, "subject_ref": str(OTHER)})).status_code == 422
    assert asyncio.run(post(app, token, suffix=f"/outline?subject_ref={OTHER}")).status_code == 400
    for role in ("admin", "leader"):
        staff_app, staff_token = make_app(
            role=role, subject=OTHER, courses=() if role == "admin" else ("backend",),
            roster=(USER,) if role == "leader" else (), global_read=role == "admin",
        )
        staff_app.state.activity_reports = store
        assert asyncio.run(post(staff_app, staff_token)).status_code == 403
    assert store.calls == 0


@pytest.mark.parametrize("change", [
    {"chapters": [{"chapter_id": "a", "lesson_ids": ["lesson-a", "lesson-a"]}]},
    {"chapters": [{"chapter_id": "a", "lesson_ids": ["lesson-a"]}] * 2},
    {"chapters": [{"chapter_id": "a", "lesson_ids": [f"lesson-{i}" for i in range(2001)]}]},
    {"chapters": [{"chapter_id": str(i), "lesson_ids": []} for i in range(101)]},
    {"from_time": "2026-01-01T03:00:00+03:00"},
    {"to_time": "2026-02-02T00:00:00Z"},
])
def test_outline_rejects_invalid_selections_before_reading(change):
    app, token = make_app()
    store = Snapshot()
    app.state.activity_reports = store
    assert asyncio.run(post(app, token, {**BODY, **change})).status_code == 422
    assert store.calls == 0


@pytest.mark.parametrize("store", [
    Snapshot(overrides={"totals": None}),
    Snapshot(overrides={"status": "stale"}),
    Snapshot(overrides={"course_id": "wrong"}),
    Snapshot([row(None, 2), row("lesson-0", 1), row("lesson-0", 1)]),
    Snapshot(failure=ProjectionPending),
    Snapshot(failure=PrivacyUnavailable),
    Snapshot(failure=ReportLimitExceeded),
])
def test_outline_never_reports_zeros_for_unavailable_or_invalid_snapshots(store):
    app, token = make_app()
    app.state.activity_reports = store
    result = asyncio.run(post(app, token))
    assert result.status_code == 503, result.text
    assert result.json()["code"] == "unavailable"
    assert "lessons" not in result.json()


def test_outline_respects_trust_and_probe_before_snapshot():
    app, token = make_app()
    store = Snapshot()
    app.state.activity_reports = store

    class NotReady:
        async def is_ready(self):
            return False

    app.state.probe = NotReady()
    assert asyncio.run(post(app, token)).status_code == 503
    app.state.trust_manifest_validated = False
    assert asyncio.run(post(app, token)).status_code == 503
    assert store.calls == 0


def test_lifetime_outline_returns_totals_with_server_bounds_and_the_same_grant_scope():
    app, token = make_app()
    store = Snapshot([row(None, 180_000, 60_000), row("lesson-0", 180_000, 60_000)])
    app.state.activity_reports = store
    result = asyncio.run(post(app, token, LIFETIME_BODY, suffix=LIFETIME_SUFFIX))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "ok"
    assert data["quality"] == "unknown"
    assert data["from_time"] == "1970-01-01T00:00:00Z"
    assert data["chapters"] == [{"chapter_id": "Глава 1", **times(180_000, 60_000)}]
    assert data["lessons"][0] == {"lesson_id": "lesson-0", **times(180_000, 60_000)}
    assert asyncio.run(post(app, "wrong", LIFETIME_BODY, suffix=LIFETIME_SUFFIX)).status_code == 401
    assert asyncio.run(post(app, token, {**LIFETIME_BODY, "course_id": "other"}, suffix=LIFETIME_SUFFIX)).status_code == 403
    assert asyncio.run(post(app, token, BODY, suffix=LIFETIME_SUFFIX)).status_code == 422
    assert asyncio.run(post(app, token, LIFETIME_BODY, suffix=LIFETIME_SUFFIX + "?course_id=backend")).status_code == 400
    assert store.calls == 1


@pytest.mark.parametrize("role", ["leader", "admin"])
def test_staff_outline_accepts_only_the_signed_course_and_student(role):
    app, token = make_app(role=role, subject=OTHER, roster=(USER,), scope="report:student")
    store = Snapshot([row(None, 40, 60), row("lesson-0", 40, 60)], lifetime_actor=OTHER, lifetime_target=USER)
    app.state.activity_reports = store

    async def run():
        async def outline(body=STUDENT_LIFETIME_BODY, suffix=STUDENT_LIFETIME_SUFFIX):
            return await post(app, token, body, suffix=suffix)

        result = await outline()
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        data = result.json()
        assert data["from_time"] == "1970-01-01T00:00:00Z"
        assert data["lessons"][0] == {"lesson_id": "lesson-0", **times(40, 60)}
        assert (await outline({**STUDENT_LIFETIME_BODY, "subject_ref": str(OTHER)})).status_code == 403
        assert (await outline({**STUDENT_LIFETIME_BODY, "course_id": "other"})).status_code == 403
        assert (await outline(LIFETIME_BODY)).status_code == 422
        assert (await outline(suffix=STUDENT_LIFETIME_SUFFIX + "?subject_ref=" + str(OTHER))).status_code == 400
        assert (await outline(LIFETIME_BODY, LIFETIME_SUFFIX)).status_code == 403
        assert (await outline(BODY, "/outline")).status_code == 403
        assert store.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("claims", [
    {},
    {"role": "leader", "roster": ()},
    {"role": "leader", "roster": (USER,)},
    {"role": "leader", "roster": (USER, OTHER)},
    {"role": "admin", "courses": (), "global_read": True},
])
def test_staff_outline_rejects_self_or_aggregate_grants(claims):
    app, token = make_app(**claims)
    store = Snapshot()
    app.state.activity_reports = store
    result = asyncio.run(post(app, token, STUDENT_LIFETIME_BODY, suffix=STUDENT_LIFETIME_SUFFIX))
    assert result.status_code == 403
    assert store.calls == 0


@pytest.mark.parametrize("role", ["leader", "admin"])
def test_student_grant_cannot_read_daily_aggregates_or_another_student(role):
    app, token = make_app(role=role, subject=OTHER, roster=(USER,), scope="report:student")

    async def run():
        params = {**PARAMS, "subject_ref": str(USER)}
        result = await request(app, token, params)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert (await request(app, token, PARAMS)).status_code == 403
        assert (await request(app, token, {**params, "subject_ref": str(OTHER)})).status_code == 403
        assert (await request(app, token, {**params, "course_id": "other"})).status_code == 403
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            details = await client.get(PATH + "/details", params=params, headers={"Authorization": f"Bearer {token}"})
            assert details.status_code == 403

    asyncio.run(run())


@pytest.mark.parametrize("claims", [
    {"role": "user", "roster": (USER,)},
    {"role": "admin", "courses": (), "global_read": True},
    {"role": "admin", "roster": (USER, OTHER)},
    {"role": "leader", "roster": ()},
    {"role": "leader", "roster": (USER,), "courses": ("backend", "other")},
])
def test_student_report_verifier_rejects_invalid_scope_shape(claims):
    app, token = make_app(**claims, scope="report:student")
    result = asyncio.run(post(app, token, STUDENT_LIFETIME_BODY, suffix=STUDENT_LIFETIME_SUFFIX))
    assert result.status_code == 401, result.text


@pytest.mark.parametrize("store", [
    Snapshot(overrides={"quality": "partial"}),
    Snapshot([row(None, 8), row("lesson-0", 4)]),
    Snapshot(failure=ReportLimitExceeded),
    object(),
])
def test_lifetime_outline_unavailable_without_proven_projection(store):
    app, token = make_app()
    app.state.activity_reports = store
    result = asyncio.run(post(app, token, LIFETIME_BODY, suffix=LIFETIME_SUFFIX))
    assert result.status_code == 503, result.text
    assert result.json()["code"] == "unavailable" and "lessons" not in result.json()


def test_query_openapi_includes_outline_without_changing_chapter_response():
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.factory import create_app

    app = create_app("query", Settings(database_url="postgresql://analytics_user@localhost:57392/analytics_test"))
    spec = app.openapi()
    outline = spec["paths"][PATH + "/outline"]["post"]
    lifetime = spec["paths"][PATH + LIFETIME_SUFFIX]["post"]
    student_lifetime = spec["paths"][PATH + STUDENT_LIFETIME_SUFFIX]["post"]
    chapters = spec["paths"][PATH + "/chapters"]["post"]
    assert outline["operationId"] == "getOutlineActivityReport"
    assert outline["security"] == chapters["security"] == [{"AnalyticsGrant": []}]
    assert outline["requestBody"] == chapters["requestBody"]
    assert outline["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/OutlineActivityReport")
    assert lifetime["security"] == outline["security"] and lifetime["responses"]["200"] == outline["responses"]["200"]
    assert set(spec["components"]["schemas"]["LifetimeActivityRequest"]["properties"]) == {"course_id", "chapters"}
    assert student_lifetime["security"] == lifetime["security"]
    assert student_lifetime["responses"] == lifetime["responses"]
    assert set(spec["components"]["schemas"]["StudentLifetimeActivityRequest"]["required"]) == {"course_id", "chapters", "subject_ref"}
    assert chapters["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ChapterActivityReport")
    assert set(outline["responses"]) == {"200", "400", "401", "403", "422", "503"}
    schema = spec["components"]["schemas"]["OutlineActivityReport"]
    assert {"lessons", "unmapped_lessons"} <= set(schema["required"])
    assert schema["properties"]["lessons"]["maxItems"] == 2000
