"""Independent Privacy acceptance for grouped learner reports on an owned 607 fixture."""
import asyncio
from collections import Counter
from contextlib import ExitStack, suppress
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.core_privacy.relay import RelayConfig
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.v2 import DeletionFence


GRANT = "/api/analytics/grants/"
REPORT = "/api/analytics/reports/activity"
SUBJECTS = ("first", "multi", "lost", "scope")
EXPECTED_MS = (10000, 20000, 30000, 40000)
EXPECTED_SECTION = "practice"


def bearer(grant):
    return {"Authorization": "Bearer " + grant["grant"]}


def token(client, mode, learner):
    response = client.post(GRANT + mode, json={"course_id": "backend"})
    assert response.status_code == 200
    grant = response.json()
    assert grant["subject_ref"] == learner["subject_ref"]
    return grant


def assert_totals(response, amount):
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    totals = response.json()["totals"]
    assert (totals["active_ms"], totals["theory_ms"], totals["practice_ms"]) == (amount, 0, amount)


def assert_denied(response, status):
    assert response.status_code == status
    assert "totals" not in response.json()


def seed_and_log_in(target, wire, learners, stack, existing):
    start = (datetime.fromisoformat(next(iter(existing.values()))["started_at"]) if existing else
             datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=3))
    path = target.ANALYTICS / "contracts/fixtures/browser-batch.json"
    source_event = json.loads(path.read_text(encoding="utf-8"))["events"][1]
    clients = []
    for alias, amount in zip(SUBJECTS, EXPECTED_MS, strict=True):
        learner = learners[alias]
        client = stack.enter_context(httpx.Client(base_url=wire.ORIGIN, timeout=10, trust_env=False,
                                          headers={"Origin": wire.ORIGIN}))
        wire.login(client, learner)
        catalog, lesson = wire.catalog(client, "backend")
        expected = dict(course_id="backend", catalog_version=catalog, lesson_id=lesson, section=EXPECTED_SECTION,
                        active_ms=amount, started_at=start, last_action_at=start,
                        ended_at=start + timedelta(milliseconds=amount))
        if existing:
            prior = existing[learner["subject_ref"]]
            for field in ("started_at", "ended_at", "last_action_at"):
                prior[field] = datetime.fromisoformat(prior[field])
            assert {key: prior[key] for key in expected} == expected
        else:
            event = dict(source_event, event_id=str(uuid4()), session_id=str(uuid4()), **{
                key: value.isoformat() if isinstance(value, datetime) else value for key, value in expected.items()})
            response = client.post("/api/analytics/events/batch", json={"events": [event]},
                                   headers=bearer(token(client, "collect", learner)))
            assert response.status_code == 200
            ack, = response.json()["items"]
            assert (ack["event_id"], ack["status"]) == (event["event_id"], "accepted")
        clients.append(client)
    return clients, dict(course_id="backend", from_time=(start - timedelta(seconds=1)).isoformat(),
                         to_time=(start + timedelta(minutes=1)).isoformat())


class GroupPause:
    """Observe memberships and optionally pause one real group; leave its guards intact."""

    def __init__(self, app):
        self.member_task = ContextVar("607_group_under_test", default=False)
        database = app.state.database
        store = app.state.activity_reports
        self.queue = store._self_reports
        self.materialize = store._materialize_report
        store._materialize_report = self.observe_materialization
        self.read_group = self.queue.read
        self.schedule = self.queue._schedule
        self.exit_guard = database.transaction_exit_guard
        assert self.exit_guard is not None, "607 must run against the actual local Privacy exit guard"
        self.queue.read = self.observe_group
        self.queue._schedule = self.observe_schedule
        database.transaction_exit_guard = self.observe_exit
        self.point = None

    def arm(self, point):
        self.point = point
        self.members = None
        self.read_counts = Counter()
        self.fault_subject = None
        self.paused = asyncio.Event()
        self.release = asyncio.Event()

    def observe_schedule(self):
        # Pause dispatch until three actual requests have entered the production queue.
        if self.point is not None and self.members is None and len(self.queue.pending) < 3:
            return
        self.schedule()

    async def observe_group(self, jobs):
        if self.point is None or self.members is not None:
            return await self.read_group(jobs)
        self.members = tuple(str(job.grant.sub) for job in jobs)
        if self.point == "sql_fault":
            assert len(self.members) == 3
            self.fault_subject = self.members[1]
        active = self.member_task.set(True)
        try:
            if self.point == "membership":
                await self.pause()
            return await self.read_group(jobs)
        finally:
            self.member_task.reset(active)

    async def observe_materialization(self, session, args, snapshot, grant, query, scope, **kwargs):
        if self.point == "sql_fault" and self.member_task.get():
            actor = str(grant.sub)
            self.read_counts[actor] += 1
            if actor == self.fault_subject:
                await session.execute(text("SELECT 1 / 0"))
        return await self.materialize(session, args, snapshot, grant, query, scope, **kwargs)

    async def observe_exit(self, session):
        if self.point in {"exit", "sql_fault"} and self.member_task.get():
            await self.pause()
        await self.exit_guard(session)

    async def pause(self):
        self.paused.set()
        await asyncio.wait_for(self.release.wait(), 5)


@pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_607_GROUPED_PRIVACY") != "1",
                    reason="explicit fresh owned 607 local three-role target required")
def test_grouped_self_reports_preserve_privacy(tmp_path, monkeypatch):
    """Use actual Core signing, Analytics PostgreSQL, source and independent Authority."""
    analytics_folder = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(analytics_folder / "tools"))
    import shadow_607_failures as wire
    import shadow_607_target as target

    run_id = UUID(os.environ["CORP607_GROUPED_RUN"])
    local, folder, _, settings = target.runtime.read_settings(str(run_id))
    learners = json.loads(local._read(folder, "corp607-learners.json", 16384))
    pending = json.loads(local._read(folder, "corp607-pending.json", 4096))
    data = target.bootstrap.config(target.CONNECTION)
    subjects = [learners[alias]["subject_ref"] for alias in SUBJECTS]

    def state(subject, control_id=None):
        name = pending["analytics"]
        with target.connect_owned(data, name) as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            target.observe_identity(pg, name, pending["analytics_owner"], pending["sentinel"]["analytics"])
            facts, = pg.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s",
                                (subject,)).fetchone()
            applied, = pg.execute("SELECT applied_through FROM analytics.privacy_state WHERE singleton").fetchone()
            receipt = pg.execute("SELECT phase, subject_ref::text FROM analytics.privacy_receipts WHERE control_id=%s",
                                 (control_id,)).fetchone() if control_id else None
            return facts, applied, receipt

    resume = os.environ.get("CORP607_GROUPED_RESUME_SEED") == "1"
    assert [state(subject) for subject in subjects] == [(int(resume), 0, None)] * len(subjects)
    existing = {}
    if resume:
        name = pending["analytics"]
        with target.connect_owned(data, name) as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            target.observe_identity(pg, name, pending["analytics_owner"], pending["sentinel"]["analytics"])
            rows = pg.execute("""SELECT subject_ref::text, accepted_event->'event'
                FROM analytics.activity_facts WHERE source_kind='browser_v2'""").fetchall()
        assert len(rows) == len(subjects) and {subject for subject, _ in rows} == set(subjects)
        existing = dict(rows)
    query_cfg = settings["query"].model_copy(update={"pool_size": 2, "pool_overflow": 0})
    assert query_cfg.activity_projection_enabled and query_cfg.activity_day_query_enabled
    assert query_cfg.issuer_mode == "local"
    query_app = create_app("query", query_cfg)
    raw_authority = query_cfg.privacy_authority_json.get_secret_value()
    authority_config = AuthorityConfig.from_json(raw_authority)
    relay = RelayConfig.from_json(settings["privacy"].core_deletion_relay_json.get_secret_value())

    with ExitStack() as stack:
        clients, query = seed_and_log_in(target, wire, learners, stack, existing)
        if resume:
            print("607.P7 prior seed reconciled read-only: 4 exact scoped browser facts, no new writes")
        local_query = stack.enter_context(TestClient(query_app))
        hook = GroupPause(query_app)

        def grants():
            return [token(client, "report", learners[alias]) for client, alias in zip(clients, SUBJECTS, strict=True)]

        initial = grants()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if local_query.get("/ready").status_code == 200:
                responses = [local_query.get(REPORT, params=query, headers=bearer(g)) for g in initial]
                if all(response.status_code == 200 for response in responses):
                    for response, value in zip(responses, EXPECTED_MS, strict=True):
                        assert_totals(response, value)
                    break
            time.sleep(.2)
        else:
            pytest.fail("real learner reports did not become ready")

        async def exercise(reports, scenario, append_control=None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=query_app), base_url="http://query.test") as client:
                async def read(index, extra=None):
                    return await client.get(REPORT, params={**query, **(extra or {})}, headers=bearer(reports[index]))

                point = {"late": "membership", "sql_fault": "sql_fault"}.get(scenario, "exit")
                hook.arm(point)
                requests = [asyncio.create_task(read(index)) for index in range(3)]
                grouped = asyncio.gather(*requests)
                try:
                    await asyncio.wait_for(hook.paused.wait(), 5)
                    assert sorted(hook.members) == sorted(subjects[:3]), "three genuine self-grants froze as one group"
                    assert not any(request.done() for request in requests), "results must wait for the production exit guard"
                    if scenario == "sql_fault":
                        assert tuple(hook.read_counts[actor] for actor in hook.members) == (2, 2, 1)
                    if scenario == "late":
                        late = asyncio.create_task(read(3))
                        wrong = asyncio.create_task(read(0, {"subject_ref": subjects[1]}))
                        await asyncio.sleep(0)
                    if scenario == "expiry":
                        expires = datetime.fromisoformat(reports[0]["expires_at"]).timestamp()
                        assert time.time() < expires
                        await asyncio.sleep(expires - time.time() + .05)
                    if scenario == "deletion":
                        await append_control()
                except BaseException:
                    grouped.cancel()
                    with suppress(asyncio.CancelledError):
                        await grouped
                    raise
                finally:
                    hook.release.set()
                results = await grouped
                hook.point = None
                if scenario == "late":
                    assert_totals(await late, EXPECTED_MS[3])
                    assert_denied(await wrong, 403)
                return results

        normal = local_query.portal.call(exercise, grants(), "late")
        for result, value in zip(normal, EXPECTED_MS[:3], strict=True):
            assert_totals(result, value)
        print("607.P7 grouped A/B/C distinct totals; late D owns result; cross-subject scope rejected")

        if os.environ.get("CORP607_GROUPED_SQL_RECOVERY") == "1":
            sql_group = local_query.portal.call(exercise, grants(), "sql_fault")
            for result, actor, value in zip(sql_group, subjects[:3], EXPECTED_MS[:3], strict=True):
                if actor == hook.fault_subject:
                    assert_denied(result, 503)
                else:
                    assert_totals(result, value)
            print("607.P7 real SQL error at middle job; replay reads 2/2/1; only its result is 503, no early release")

        expired = token(clients[0], "report", learners[SUBJECTS[0]])
        expires_at = datetime.fromisoformat(expired["expires_at"]).timestamp()
        time.sleep(max(0, expires_at - time.time() - 2.5))
        expiring = [expired, *(token(clients[ix], "report", learners[SUBJECTS[ix]]) for ix in range(1, 4))]
        expired_group = local_query.portal.call(exercise, expiring, "expiry")
        assert_denied(expired_group[0], 403)
        assert_totals(expired_group[1], EXPECTED_MS[1])
        assert_totals(expired_group[2], EXPECTED_MS[2])
        print("607.P7 A expired after group entry; B/C totals admitted independently")

        control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                                 subject_ref=UUID(subjects[0]), epoch=1, occurred_at=datetime.now(timezone.utc))
        result_before = grants()

        async def hold_until_authority_proof_is_ahead():
            receipt = await AuthorityClient(authority_config).append(control, producer=relay.producer,
                producer_token=relay.token(), command_key=relay.command_key)
            assert receipt.seq == 1
            runtime = query_app.state.privacy_runtime
            async with asyncio.timeout(3):
                while runtime._latest is None or runtime._latest.frame.high_watermark < receipt.seq:
                    await asyncio.sleep(.015)
            observed = await asyncio.to_thread(state, subjects[0], control.control_id)
            assert observed[0:2] == (1, 0), "the group still holds the deleted subject lock before release"

        closed = local_query.portal.call(exercise, result_before, "deletion", hold_until_authority_proof_is_ahead)
        for response in closed:
            assert_denied(response, 503)
        after_deadline = time.monotonic() + 12
        while time.monotonic() < after_deadline:
            if state(subjects[0], control.control_id) == (0, 1, ("complete", subjects[0])) and local_query.get("/ready").status_code == 200:
                break
            time.sleep(.05)
        else:
            pytest.fail("the real Privacy worker did not apply and purge the test deletion")
        assert_denied(local_query.get(REPORT, params=query, headers=bearer(result_before[0])), 403)
        for index in (1, 2, 3):
            assert_totals(local_query.get(REPORT, params=query, headers=bearer(result_before[index])), EXPECTED_MS[index])
            assert state(subjects[index])[0] == 1
        print("607.P7 signed deletion after read closed the whole unreleased group; replay purged A; B/C/D unchanged")

