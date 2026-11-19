import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pymentor_core.adapters.sql import role_admin_commands as commands, role_admin_reads as reads
from pymentor_core.app import course_context
from pymentor_core.modules.analytics_delivery.grants import authorize_grant

ACTOR = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
FIRST = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SECOND = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class Result:
    def __init__(self, value): self.value = value
    def mappings(self): return self
    def one_or_none(self): return self.value
    def all(self): return self.value
    def scalar_one(self): return self.value
    def scalar_one_or_none(self): return self.value


class DB:
    def __init__(self, role, roster=()): self.role, self.roster = role, list(roster)
    def execute(self, statement, params):
        sql = str(statement)
        if "SELECT u.is_admin,l.course_id" in sql:
            return Result({"is_admin": self.role == "admin", "course_id": "backend" if self.role == "leader" else None})
        if "SELECT DISTINCT u.id" in sql:
            rows = [{"id": uid, "lifecycle_id": ref} for uid, ref in self.roster
                    if (not params["ref"] or params["ref"] == ref) and (params["uid"] is None or params["uid"] == uid)]
            return Result(rows[:101])
        if "SELECT EXISTS(SELECT 1 FROM course_leadership" in sql:
            return Result(False)
        if "SELECT lifecycle_id FROM account_lifecycles" in sql:
            return Result({1: ACTOR, 2: FIRST, 3: SECOND}.get(params["uid"]))
        if "FROM booking_history h WHERE h.course_id=:course AND h.student_id=u.id" in sql:
            assert "booking_quotas" not in sql
            return Result(any(uid == params["uid"] for uid, _ in self.roster))
        raise AssertionError("unexpected SQL")


@pytest.fixture
def policy(monkeypatch):
    principal = SimpleNamespace(user_id=1, account_ref=ACTOR)
    refs = {1: ACTOR, 2: FIRST, 3: SECOND}
    monkeypatch.setattr(commands, "_lock_users", lambda db, ids: {uid: {"is_admin": False} for uid in ids})
    monkeypatch.setattr(commands, "_ref", lambda db, uid: refs.get(uid))
    monkeypatch.setattr(commands, "_authority", lambda db, principal, users, course: ("leader" if course else "admin", None, 0))
    monkeypatch.setattr(reads, "authorize", lambda db, principal, course: None if course == "backend" else (_ for _ in ()).throw(HTTPException(403)))
    monkeypatch.setattr(reads, "_require_reconciled", lambda db: None)
    entries = {"backend": SimpleNamespace(course_id="backend", enabled=True),
               "backend-copy": SimpleNamespace(course_id="backend-copy", enabled=True)}
    registry = SimpleNamespace(entries=entries, require_enabled=lambda course: entries[course])
    monkeypatch.setattr(course_context, "default_runtime", lambda: SimpleNamespace(registry=registry))
    return principal


def test_user_self_report_and_collect_are_server_scoped(policy):
    db = DB("user")
    report = authorize_grant(db, policy, purpose="report", course_id=None, target_ref=None)
    assert report["scope"] == "report:read" and report["subjects"] == []
    assert report["courses"] == ["backend", "backend-copy"]
    collect = authorize_grant(db, policy, purpose="collect", course_id="backend", target_ref=None)
    assert collect["courses"] == ["backend"] and collect["subject"] == ACTOR
    with pytest.raises(HTTPException) as denied:
        authorize_grant(db, policy, purpose="report", course_id="backend", target_ref=SECOND)
    assert denied.value.status_code == 403
    own = authorize_grant(db, policy, purpose="report", course_id=None, target_ref=None, target_user_id=1)
    assert own["target_subject_ref"] == ACTOR
    with pytest.raises(HTTPException) as denied:
        authorize_grant(db, policy, purpose="report", course_id=None, target_ref=None, target_user_id=2)
    assert denied.value.status_code == 403


def test_platform_tool_grant_is_collection_only_for_the_signed_in_learner(policy):
    scope = "platform-tools"
    result = authorize_grant(DB("user"), policy, purpose="collect", course_id=scope, target_ref=None)
    assert result == {"subject": ACTOR, "role": "user", "scope": "collect", "courses": [scope],
                      "subjects": [], "global_read": False}
    for role, purpose, target, expected in (
        ("user", "report", None, 404), ("user", "collect", SECOND, 403),
        ("leader", "collect", None, 403), ("admin", "collect", None, 403),
    ):
        with pytest.raises(HTTPException) as exc:
            authorize_grant(DB(role), policy, purpose=purpose, course_id=scope, target_ref=target)
        assert exc.value.status_code == expected


@pytest.mark.parametrize("role", ["user", "leader", "admin"])
def test_internal_self_report_for_each_role(policy, role):
    claims = authorize_grant(DB(role), policy, purpose="self_report", course_id=None, target_ref=None)
    assert claims["role"] == role and claims["subject"] == ACTOR and claims["subjects"] == []
    assert claims["global_read"] is (role == "admin")
    with pytest.raises(HTTPException):
        authorize_grant(DB(role), policy, purpose="self_report", course_id=None, target_ref=SECOND)


def test_measured_calendar_http_scopes_learner_and_rechecks_lifecycle(policy, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pymentor_core.adapters.course_local.packages import CourseNotFoundError
    from pymentor_core.modules.identity.api import learner_analytics

    role = "user"
    observed = []
    echo = "backend"
    rotate_on_read = False
    signed_claims = []

    def sign(**claims):
        signed_claims.append(claims)
        return "synthetic-grant"

    async def principal(request):
        return policy

    async def run_sql(request, operation):
        return operation(DB(role))

    async def read(kind, grant, params):
        observed.append((kind, grant, params))
        if rotate_on_read:
            monkeypatch.setattr(commands, "_ref", lambda db, uid: SECOND)
        result = {"days": ["2026-11-05"], "current_streak": None, "streak_is_lower_bound": False,
                  "coverage": "unknown", "as_of": None}
        if echo is not None:
            result["course_id"] = echo
        return result

    def enable(course):
        if course != "backend":
            raise CourseNotFoundError(course)

    monkeypatch.setattr(learner_analytics, "async_current_principal", principal)
    monkeypatch.setattr(learner_analytics, "run_request_sql", run_sql)
    monkeypatch.setattr(course_context.default_runtime().registry, "require_enabled", enable)
    app = FastAPI()
    app.include_router(learner_analytics.router, prefix="/api/user")
    app.state.analytics_grant_signer = SimpleNamespace(grant=sign)
    app.state.analytics_learner_client = SimpleNamespace(read=read)
    with TestClient(app) as client:
        path = "/api/user/activity/measured?year=2026&month=11"
        scoped = path + "&course_id=backend"
        response = client.get(scoped)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["course_id"] == "backend" and response.json()["days"] == ["2026-11-05"]
        assert observed == [("measured", "synthetic-grant", {"year": 2026, "month": 11, "course_id": "backend"})]
        assert signed_claims == [{"subject": ACTOR, "role": "user", "scope": "report:read",
                                   "courses": ["backend"], "subjects": [], "global_read": False}]
        assert client.get(scoped + "&course_id=backend").status_code == 422
        assert client.get(scoped + f"&subject_ref={SECOND}").status_code == 422
        assert client.get(path + "&course_id=disabled").status_code == 404
        assert client.get(path + "&course_id=platform-tools").status_code == 404
        for staff_role in ("leader", "admin"):
            role = staff_role
            assert client.get(scoped).status_code == 403
        assert len(observed) == 1

        role = "user"
        echo = "backend-copy"
        assert client.get(scoped).status_code == 503
        echo = None
        response = client.get(scoped)
        assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
        response = client.get(path)
        assert response.status_code == 200 and "course_id" not in response.json()

        echo = "backend"
        rotate_on_read = True
        response = client.get(scoped)
        assert response.status_code == 403 and "days" not in response.json()


def test_leader_roster_all_or_validated_one(policy):
    db = DB("leader", [(2, FIRST), (3, SECOND)])
    grant = authorize_grant(db, policy, purpose="report", course_id="backend", target_ref=None)
    assert grant["subjects"] == [FIRST, SECOND] and grant["global_read"] is False
    grant = authorize_grant(db, policy, purpose="report", course_id="backend", target_ref=FIRST)
    assert grant["subjects"] == [FIRST]
    grant = authorize_grant(db, policy, purpose="report", course_id="backend", target_ref=None, target_user_id=2)
    assert grant["subjects"] == [FIRST] and grant["target_subject_ref"] == FIRST
    with pytest.raises(HTTPException) as denied:
        authorize_grant(db, policy, purpose="report", course_id="backend", target_ref=None, target_user_id=4)
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as denied:
        authorize_grant(db, policy, purpose="report", course_id="other", target_ref=None)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        authorize_grant(db, policy, purpose="report", course_id="backend", target_ref="dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    assert denied.value.status_code == 404


def test_large_or_incomplete_roster_fails_closed(policy):
    with pytest.raises(HTTPException) as large:
        authorize_grant(DB("leader", [(i, FIRST) for i in range(2, 103)]), policy,
                        purpose="report", course_id="backend", target_ref=None)
    assert large.value.status_code == 503
    with pytest.raises(HTTPException) as missing:
        authorize_grant(DB("leader", [(2, None)]), policy, purpose="report", course_id="backend", target_ref=None)
    assert missing.value.status_code == 503


def test_admin_numeric_target_resolves_current_lifecycle(policy, monkeypatch):
    monkeypatch.setattr(reads, "_enabled", lambda course: None)
    result = authorize_grant(DB("admin"), policy, purpose="report", course_id="backend", target_ref=None, target_user_id=2)
    assert result["global_read"] is True and result["target_subject_ref"] == FIRST
    with pytest.raises(HTTPException) as unavailable:
        authorize_grant(DB("admin"), policy, purpose="report", course_id=None, target_ref=None, target_user_id=4)
    assert unavailable.value.status_code == 503


def test_http_grant_shape_and_scope(policy, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import jwt
    from pymentor_core.modules.analytics_delivery import router as grant_router
    from pymentor_core.modules.analytics_delivery.signing import Signer
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    app = FastAPI()
    app.state.analytics_grant_signer = Signer(issuer="issuer", kid="kid", private_pem=pem)
    app.include_router(grant_router.router, prefix="/api")
    async def principal(request): return policy
    async def run_sql(request, operation): return operation(DB("user"))
    monkeypatch.setattr(grant_router, "request_principal", principal)
    monkeypatch.setattr(grant_router, "run_request_sql", run_sql)
    with TestClient(app) as client:
        response = client.post("/api/analytics/grants/report", json={})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        body = response.json()
        assert set(body) == {"grant", "subject_ref", "expires_at", "server_time"}
        claims = jwt.decode(body["grant"], key.public_key(), algorithms=["EdDSA"], audience="analytics", issuer="issuer")
        assert body["subject_ref"] == ACTOR == claims["sub"]
        assert claims["scope"] == "report:read" and claims["exp"] - claims["iat"] == 60
        assert client.post("/api/analytics/grants/report", json={"subject_ref": SECOND}).status_code == 403
        own = client.post("/api/analytics/grants/report", json={"user_id": 1})
        assert own.status_code == 200 and own.json()["target_subject_ref"] == ACTOR
        assert client.post("/api/analytics/grants/report", json={"user_id": 2}).status_code == 403
        assert client.post("/api/analytics/grants/report", json={"user_id": 1, "subject_ref": ACTOR}).status_code == 422
        assert client.post("/api/analytics/grants/report", json={"user_id": True}).status_code == 422
        response = client.post("/api/analytics/grants/collect", json={"course_id": "backend"})
        assert response.status_code == 200
        claims = jwt.decode(response.json()["grant"], key.public_key(), algorithms=["EdDSA"], audience="analytics", issuer="issuer")
        assert claims["scope"] == "collect" and claims["exp"] - claims["iat"] == 300
        async def leader_sql(request, operation): return operation(DB("leader", [(2, FIRST), (3, SECOND)]))
        monkeypatch.setattr(grant_router, "run_request_sql", leader_sql)
        response = client.post("/api/analytics/grants/report", json={"course_id": "backend", "user_id": 2})
        assert response.status_code == 200 and response.json()["target_subject_ref"] == FIRST
        claims = jwt.decode(response.json()["grant"], key.public_key(), algorithms=["EdDSA"], audience="analytics", issuer="issuer")
        assert claims["role"] == "leader" and claims["subject_refs"] == [FIRST]
        assert client.post("/api/analytics/grants/report", json={"course_id": "backend", "user_id": 4}).status_code == 404
        async def admin_sql(request, operation): return operation(DB("admin"))
        monkeypatch.setattr(grant_router, "run_request_sql", admin_sql)
        response = client.post("/api/analytics/grants/report", json={"user_id": 2})
        assert response.status_code == 200 and response.json()["target_subject_ref"] == FIRST
        claims = jwt.decode(response.json()["grant"], key.public_key(), algorithms=["EdDSA"], audience="analytics", issuer="issuer")
        assert claims["role"] == "admin" and claims["global_read"] is True

        path = "/api/analytics/grants/report"
        opted_in = {"student_statistics": True, "course_id": "backend", "user_id": 3,
                    "expected_actor_ref": ACTOR, "expected_target_ref": SECOND}
        selection = None

        async def read_selection(request, actor, course, target):
            return selection if (actor, course, target) == (ACTOR, "backend", SECOND) else None

        monkeypatch.setattr(grant_router, "statistics_selection_revision", read_selection)

        def authorize_course(db, principal, users, course):
            if db.role == "user" or course != "backend":
                raise HTTPException(403)
            return db.role, None, 7

        monkeypatch.setattr(commands, "_authority", authorize_course)

        def verify_student_grant(response, role):
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            body = response.json()
            assert body["subject_ref"] == ACTOR and body["target_subject_ref"] == SECOND
            claims = jwt.decode(body["grant"], key.public_key(), algorithms=["EdDSA"], audience="analytics", issuer="issuer")
            assert claims["scope"] == "report:student" and claims["role"] == role
            assert claims["global_read"] is False and claims["exp"] - claims["iat"] == 60
            assert claims["course_ids"] == ["backend"] and claims["subject_refs"] == [SECOND]

        verify_student_grant(client.post(path, json=opted_in), "admin")
        monkeypatch.setattr(grant_router, "run_request_sql", run_sql)
        assert client.post(path, json=opted_in).status_code == 403

        async def leader_without_roster(request, operation):
            return operation(DB("leader"))

        monkeypatch.setattr(grant_router, "run_request_sql", leader_without_roster)
        assert client.post(path, json=opted_in).status_code == 403
        selection = 6
        assert client.post(path, json=opted_in).status_code == 403
        selection = 7
        verify_student_grant(client.post(path, json=opted_in), "leader")
        assert client.post(path, json={**opted_in, "course_id": "other"}).status_code == 403
        assert client.post(path, json={**opted_in, "user_id": 2}).status_code == 404
        assert client.post(path, json={**opted_in, "expected_actor_ref": SECOND}).status_code == 401
        assert client.post(path, json={**opted_in, "student_statistics": 1}).status_code == 422
        assert client.post("/api/analytics/grants/collect", json=opted_in).status_code == 422
