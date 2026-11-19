"""Prepared a011 Query checks; fixture requires explicit disposable-DB GO."""
import asyncio
import importlib.util
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.postgres import activity_projection_reports as report_module
from pymentor_analytics.adapters.postgres import course_study_time as study_module
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport, ProjectionUnavailable
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded
from pymentor_analytics.adapters.postgres.course_study_time import read_course_study_time
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.course_visitors import CourseStudyTimeAggregation, CourseStudyTimeGrant, CourseVisitorDiscovery
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.reports_v1 import ActivityLifetimeQuery, ActivityQuery
from pymentor_analytics.modules.analytics.activity.calculator import calculate
from pymentor_analytics.modules.analytics.activity.day import normalize_day
from pymentor_analytics.modules.analytics.activity.measured_calendar import _query, read_measured_calendar

_path = Path(__file__).parents[1] / "activity_projection/test_projection_reports_pg.py"
_spec = importlib.util.spec_from_file_location("day_query_bucket_helpers", _path)
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
g = helpers.g


async def _drain_day(store):
    owner = uuid4()
    for _ in range(200):
        phase = await store.backfill_step(deadline=time.monotonic()+10)
        while True:
            claim = await store.claim(owner, deadline=time.monotonic()+10)
            if claim is None: break
            loaded, buckets = await store.load(claim, deadline=time.monotonic()+10)
            await store.commit(loaded, normalize_day(buckets), deadline=time.monotonic()+10)
        if phase == "complete": return
    raise AssertionError("day backfill did not finish")


async def _seed(urls, run_id, *, include_older_activity=False):
    writer, worker = helpers.db(urls["collector"]), helpers.db(urls["processor"])
    start = g.BASE + timedelta(hours=21)  # midnight at fixed UTC+03
    events = [
        g.event(start-timedelta(seconds=10), start+timedelta(seconds=5), number=1),
        g.event(start-timedelta(seconds=5), start+timedelta(seconds=5), number=2,
                course="backend_copy", section="practice"),
        g.event(start+timedelta(hours=12), start+timedelta(hours=12, seconds=10), number=3,
                subject=g.OTHER, section="practice"),
        g.event(start+timedelta(hours=12), start+timedelta(hours=12), number=4,
                subject=g.OTHER, opened=True),
        g.event(start+timedelta(days=1), start+timedelta(days=1, seconds=5), number=5),
    ]
    if include_older_activity:
        older = start - timedelta(days=60)
        events.append(g.event(older, older + timedelta(seconds=30), number=6))
    try:
        async with writer.transaction() as session:
            for event in events: assert await ActivityStorage(session).insert(event) == "inserted"
        await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
        await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))
        return start, events
    finally:
        await writer.close()
        await worker.close()


def _grant():
    return VerifiedGrant(sub=uuid4(), role="admin", scope="report:read", course_ids=(), subject_refs=(),
                         global_read=True, expires_at=int(time.time())+120)


def test_course_calendar_matches_selected_student_report_and_excludes_open_only_days(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target

    async def run():
        start, _ = await _seed(urls, run_id)
        writer, worker, reader = (helpers.db(urls[role]) for role in ("collector", "processor", "query"))
        store = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id, day_projection_enabled=True)
        learner = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend", "backend_copy"), (), False, int(time.time()) + 120)
        leader = VerifiedGrant(uuid4(), "leader", "report:student", ("backend",), (g.SUBJECT,), False, learner.expires_at)
        other_course = start + timedelta(days=2)
        opened_only = start + timedelta(days=3)
        other_user = start + timedelta(days=4)
        first_course = start + timedelta(hours=1)
        events = [
            g.event(other_course, other_course + timedelta(seconds=5), number=71, course="backend_copy"),
            g.event(opened_only, opened_only, number=72, opened=True),
            g.event(other_user, other_user + timedelta(seconds=5), number=73, subject=g.OTHER),
            g.event(first_course, first_course + timedelta(seconds=5), number=74),
        ]
        try:
            async with writer.transaction() as session:
                for event in events:
                    assert await ActivityStorage(session).insert(event) == "inserted"
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))

            now = opened_only + timedelta(hours=1)
            backend = await read_measured_calendar(store, learner, 2026, 1, now=now, course_id="backend")
            extra = await read_measured_calendar(store, learner, 2026, 1, now=now, course_id="backend_copy")
            singleton = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend",), (), False, learner.expires_at)
            unqualified = await read_measured_calendar(store, singleton, 2026, 1, now=now)
            query = _query(date(2026, 1, 1), date(2026, 2, 1), "backend").model_copy(update={"subject_ref": g.SUBJECT})
            staff = await store.read_activity(leader, query)
            assert backend["days"] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
            assert "course_id" not in unqualified
            assert unqualified["days"] == backend["days"]
            assert unqualified["current_streak"] == backend["current_streak"]
            assert backend["days"] == [row["date"] for row in staff["daily"] if row["active_ms"] > 0]
            assert any(row["date"] == date(2026, 1, 5) and row["active_ms"] == 0 and row["unique_visitors"] == 1
                       for row in staff["daily"])
            assert extra["days"] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 4)]
            assert backend["current_streak"] is None
            assert extra["current_streak"] == 1 and extra["streak_is_lower_bound"]
        finally:
            await writer.close()
            await worker.close()
            await reader.close()

    asyncio.run(run())


def test_lifetime_reads_old_activity_for_one_learner_without_daily_rows(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target

    async def run():
        start, events = await _seed(urls, run_id, include_older_activity=True)
        reader = helpers.db(urls["query"])
        migrate = helpers.db(urls["migrate"])
        own = VerifiedGrant(sub=g.SUBJECT, role="user", scope="report:read", course_ids=("backend",),
                            subject_refs=(), global_read=False, expires_at=int(time.time()) + 120)
        leader = VerifiedGrant(sub=uuid4(), role="leader", scope="report:student", course_ids=("backend",),
                               subject_refs=(g.SUBJECT,), global_read=False, expires_at=own.expires_at)
        admin = VerifiedGrant(sub=uuid4(), role="admin", scope="report:student", course_ids=("backend",),
                              subject_refs=(g.SUBJECT,), global_read=False, expires_at=own.expires_at)
        lifetime = ActivityLifetimeQuery(course_id="backend", to_time=start + timedelta(days=2))
        selected = lifetime.model_copy(update={"subject_ref": g.SUBJECT})
        recent = ActivityQuery(from_time=start - timedelta(days=10), to_time=lifetime.to_time,
                               course_id="backend", subject_ref=None)
        try:
            for use_days in (False, True):
                store = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                                         day_projection_enabled=use_days)
                actual = await store.read_lifetime_activity(own, lifetime)
                limited = await store.read_activity(own, recent)
                expected = calculate(events, start=lifetime.from_time, end=lifetime.to_time,
                                     as_of=actual["as_of"], course_id="backend", subject_ref=g.SUBJECT,
                                     include_breakdown=True)
                assert "daily" not in actual
                assert actual["totals"] == expected["totals"]
                assert actual["breakdown"] == expected["breakdown"]
                assert actual["totals"]["active_ms"] - limited["totals"]["active_ms"] == 30_000
                for staff in (leader, admin):
                    staff_snapshot = await store.read_lifetime_activity(staff, selected)
                    assert staff_snapshot.pop("as_of") >= actual["as_of"]
                    assert staff_snapshot == {key: value for key, value in actual.items() if key != "as_of"}
                    with pytest.raises(AuthorizationError):
                        await store.read_lifetime_activity(staff, selected.model_copy(update={"subject_ref": g.OTHER}))
                with pytest.raises(AuthorizationError):
                    await store.read_lifetime_activity(own, lifetime.model_copy(update={"course_id": "backend_copy"}))
                with pytest.raises(AuthorizationError):
                    await store.read_lifetime_activity(_grant(), lifetime)
            async with migrate.transaction() as session:
                await session.execute(text("""INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id)
                    VALUES (:subject,1,true,:control)"""), {"subject": g.SUBJECT, "control": uuid4()})
            for use_days in (False, True):
                store = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                                         day_projection_enabled=use_days)
                with pytest.raises(AuthorizationError):
                    await store.read_lifetime_activity(leader, selected)
        finally:
            await reader.close()
            await migrate.close()

    asyncio.run(run())


def test_day_query_full_partial_overlap_missing_payload_and_flag_off(activity_day_projection_target):
    _,_,urls,run_id,_ = activity_day_projection_target
    async def run():
        start, events = await _seed(urls, run_id)
        reader, migrate = helpers.db(urls["query"]), helpers.db(urls["migrate"])
        day = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id, day_projection_enabled=True)
        old = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id)
        grant = _grant()
        try:
            for lo, hi in ((start, start+timedelta(days=1)),
                           (start-timedelta(seconds=5), start+timedelta(days=1, seconds=5)),
                           (start-timedelta(seconds=5), start+timedelta(seconds=5))):
                for course in (None, "backend", "backend_copy"):
                    query=ActivityQuery(from_time=lo, to_time=hi, course_id=course, subject_ref=None)
                    actual=await day.read_activity(grant,query,include_breakdown=True)
                    expected=calculate(events,start=lo,end=hi,as_of=actual["as_of"],course_id=course,include_breakdown=True)
                    previous=await old.read_activity(grant,query,include_breakdown=True)
                    assert (actual["totals"],actual["daily"],actual["breakdown"]) == (expected["totals"],expected["daily"],expected["breakdown"])
                    assert (actual["totals"],actual["daily"],actual["breakdown"]) == (previous["totals"],previous["daily"],previous["breakdown"])
            full_query=ActivityQuery(from_time=start,to_time=start+timedelta(days=1),course_id=None,subject_ref=None)
            member=VerifiedGrant(sub=g.SUBJECT,role="user",scope="report:read",course_ids=("backend",),subject_refs=(),
                                 global_read=False,expires_at=int(time.time())+120)
            own=await day.read_activity(member,full_query,include_breakdown=True)
            own_expected=calculate(events,start=start,end=start+timedelta(days=1),as_of=own["as_of"],
                                   course_id="backend",subject_ref=g.SUBJECT,include_breakdown=True)
            assert (own["totals"],own["daily"],own["breakdown"])==(own_expected["totals"],own_expected["daily"],own_expected["breakdown"])
            with pytest.raises(AuthorizationError):
                await day.read_activity(member,full_query.model_copy(update={"subject_ref":g.OTHER}))
            lesson_query=ActivityQuery(from_time=start,to_time=start+timedelta(days=1),course_id="backend",
                                       subject_ref=None,lesson_id="same-lesson")
            lesson_actual=await day.read_activity(grant,lesson_query,include_breakdown=True)
            lesson_expected=calculate(events,start=start,end=start+timedelta(days=1),as_of=lesson_actual["as_of"],
                                      course_id="backend",lesson_id="same-lesson",include_breakdown=True)
            assert (lesson_actual["totals"],lesson_actual["daily"],lesson_actual["breakdown"]) == (
                lesson_expected["totals"],lesson_expected["daily"],lesson_expected["breakdown"])
            future=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(days=1)
            empty=await day.read_activity(grant,ActivityQuery(from_time=future,to_time=future+timedelta(days=1),course_id=None,subject_ref=None))
            assert empty["totals"]["unique_visitors"]==0 and empty["daily"]==[] and empty["as_of"] is None
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_day_backfill SET phase='draining' WHERE singleton=true"))
            with pytest.raises(ProjectionUnavailable): await day.read_activity(grant,full_query)
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_day_backfill SET phase='complete' WHERE singleton=true"))
                await session.execute(text("UPDATE analytics.activity_projection_day_sources SET generation=generation+1 WHERE local_day=:day"),
                                      {"day":(start+timedelta(hours=3)).date()})
            with pytest.raises(ProjectionUnavailable): await day.read_activity(grant,full_query)
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_day_sources SET generation=generation-1 WHERE local_day=:day"),
                                      {"day":(start+timedelta(hours=3)).date()})
            async with migrate.transaction() as session:
                originals=(await session.execute(text("""SELECT contributions,presence_labels FROM analytics.activity_projection_days
                    WHERE subject_ref=:subject AND local_day=:day"""),
                    {"subject":g.SUBJECT,"day":(start+timedelta(hours=3)).date()})).one()
                await session.execute(text("""UPDATE analytics.activity_projection_days
                    SET contributions=CAST(:payload AS jsonb) WHERE subject_ref=:subject AND local_day=:day"""),
                    {"payload":json.dumps([{"course_id":"backend","lesson_id":"same-lesson","section":"theory","active_ms":0}]),
                     "subject":g.SUBJECT,"day":(start+timedelta(hours=3)).date()})
            with pytest.raises(ProjectionUnavailable): await day.read_activity(grant,full_query)
            async with migrate.transaction() as session:
                await session.execute(text("""UPDATE analytics.activity_projection_days SET contributions=CAST(:payload AS jsonb),
                    presence_labels='[]'::jsonb WHERE subject_ref=:subject AND local_day=:day"""),
                    {"payload":json.dumps(originals[0]),"subject":g.SUBJECT,"day":(start+timedelta(hours=3)).date()})
            with pytest.raises(ProjectionUnavailable): await day.read_activity(grant,full_query)
            async with migrate.transaction() as session:
                await session.execute(text("""UPDATE analytics.activity_projection_days SET presence_labels=CAST(:payload AS jsonb)
                    WHERE subject_ref=:subject AND local_day=:day"""),
                    {"payload":json.dumps(originals[1]),"subject":g.SUBJECT,"day":(start+timedelta(hours=3)).date()})
            async with migrate.transaction() as session:
                await session.execute(text("DELETE FROM analytics.activity_projection_days WHERE local_day=:day"), {"day": (start+timedelta(hours=3)).date()})
            with pytest.raises(ProjectionUnavailable): await day.read_activity(grant,full_query)
            assert (await old.read_activity(grant,full_query))["totals"]["unique_visitors"] > 0
        finally:
            await reader.close()
            await migrate.close()
    asyncio.run(run())


def test_day_query_original_bucket_and_compact_caps(activity_day_projection_target, monkeypatch):
    _,_,urls,run_id,_ = activity_day_projection_target
    async def run():
        start,_ = await _seed(urls,run_id)
        reader, migrate = helpers.db(urls["query"]), helpers.db(urls["migrate"])
        reports=PostgresProjectionActivityReport(reader,None,expected_run_id=run_id,day_projection_enabled=True)
        grant=_grant()
        query=ActivityQuery(from_time=start,to_time=start+timedelta(days=1),course_id=None,subject_ref=None)
        try:
            async with migrate.transaction() as session:
                counts=(await session.execute(text("""SELECT sum(source_bucket_count),sum(source_compact_items),sum(source_compact_bytes)
                    FROM analytics.activity_projection_days WHERE local_day=:day"""),{"day":(start+timedelta(hours=3)).date()})).one()
            for name,exact in zip(("MAX_BUCKET_ROWS","MAX_FULL_ITEMS","MAX_FULL_BYTES"),counts):
                assert exact>0
                with monkeypatch.context() as scoped:
                    scoped.setattr(report_module,name,int(exact))
                    await reports.read_activity(grant,query)
                    scoped.setattr(report_module,name,int(exact)-1)
                    with pytest.raises(ReportLimitExceeded): await reports.read_activity(grant,query)
            partial=ActivityQuery(from_time=start-timedelta(seconds=5),to_time=start+timedelta(days=1,seconds=5),course_id=None,subject_ref=None)
            async with migrate.transaction() as session:
                edge=(await session.execute(text("""SELECT sum(edge_item_count),sum(edge_json_bytes)
                    FROM analytics.activity_projection_buckets WHERE bucket_start IN (:left_bucket,:right_bucket)"""),
                    {"left_bucket":start-timedelta(minutes=15),"right_bucket":start+timedelta(days=1)})).one()
            for name,exact in zip(("MAX_EDGE_ITEMS","MAX_EDGE_BYTES"),edge):
                assert exact>0
                with monkeypatch.context() as scoped:
                    scoped.setattr(report_module,name,int(exact))
                    await reports.read_activity(grant,partial)
                    scoped.setattr(report_module,name,int(exact)-1)
                    with pytest.raises(ReportLimitExceeded): await reports.read_activity(grant,partial)
        finally:
            await reader.close()
            await migrate.close()
    asyncio.run(run())


def test_course_study_time_counts_only_positive_winning_time_and_fences(
    activity_day_projection_target, monkeypatch,
):
    from hashlib import sha256

    _, _, urls, run_id, _ = activity_day_projection_target

    async def run():
        writer, worker, reader, migrate = (helpers.db(urls[role]) for role in ("collector", "processor", "query", "migrate"))
        midnight = g.BASE + timedelta(hours=21)
        cutoff = midnight + timedelta(seconds=6, microseconds=456789)
        actor, paid, free, staff, opened, elsewhere = [uuid4() for _ in range(6)]
        events = [
            g.event(midnight - timedelta(seconds=8), midnight + timedelta(seconds=9), number=1, subject=paid),
            g.event(midnight - timedelta(seconds=3), midnight + timedelta(seconds=2), number=2,
                    subject=paid, course="backend_copy"),
            g.event(midnight + timedelta(seconds=3), midnight + timedelta(seconds=7), number=3, subject=paid),
            g.event(midnight, midnight + timedelta(seconds=4), number=4, subject=free,
                    received=midnight + timedelta(seconds=20)),
            g.event(midnight + timedelta(seconds=1), midnight + timedelta(seconds=6), number=5,
                    subject=free, received=midnight + timedelta(seconds=20)),
            g.event(midnight, midnight + timedelta(seconds=7), number=6, subject=staff),
            g.event(midnight, midnight, number=7, subject=opened, opened=True),
            g.event(midnight, midnight + timedelta(seconds=7), number=8, subject=elsewhere, course="backend_copy"),
        ]
        nonce = "a" * 32

        def digest(subject):
            return sha256((nonce + str(subject)).encode()).hexdigest()

        def grant():
            stamp = int(time.time())
            return CourseStudyTimeGrant(grant_version=1, aud="analytics-course-study-time", iss="core",
                                        sub=actor, iat=stamp, nbf=stamp, exp=stamp + 15,
                                        course_id="backend", body_sha256="0" * 64)

        discovery = CourseVisitorDiscovery(at=cutoff, nonce=nonce, operation="discover")
        aggregate = CourseStudyTimeAggregation(
            at=cutoff, nonce=nonce, operation="aggregate", active_subscriptions=[digest(paid)],
            inactive_subscriptions=[digest(free)], excluded_subjects=[digest(staff)],
        )
        bucket_worker = PostgresActivityProjection(worker, expected_run_id=run_id)
        day_worker = PostgresActivityDayProjection(worker, expected_run_id=run_id)
        try:
            async with writer.transaction() as session:
                storage = ActivityStorage(session)
                for event in events:
                    assert await storage.insert(event) == "inserted"
                assert await storage.insert(events[0]) == "duplicate"
            await helpers.drain(bucket_worker)
            await _drain_day(day_worker)
            for use_days in (False, True):
                reports = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                                           day_projection_enabled=use_days)
                candidates = await read_course_study_time(reports, grant(), discovery)
                assert candidates.subject_digests == sorted(digest(subject) for subject in (paid, free, staff))
                report = await read_course_study_time(reports, grant(), aggregate)
                assert report.quality == "unknown" and len(report.daily) == 30
                assert report.daily[-3].model_dump(exclude={"date"}) == {
                    "with_subscription": None, "without_subscription": None,
                }
                assert report.daily[-2].model_dump(mode="json") == {
                    "date": "2026-01-01", "with_subscription": {"active_ms": 5_000, "learners": 1},
                    "without_subscription": None,
                }
                assert report.daily[-1].model_dump(mode="json") == {
                    "date": "2026-01-02", "with_subscription": {"active_ms": 4_456, "learners": 1},
                    "without_subscription": {"active_ms": 6_000, "learners": 1},
                }
                wrong_memberships = aggregate.model_copy(update={"excluded_subjects": [digest(opened)]})
                with pytest.raises(ProjectionUnavailable):
                    await read_course_study_time(reports, grant(), wrong_memberships)

            reports = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id, day_projection_enabled=True)
            async with writer.transaction() as session:
                foreign = g.event(midnight, midnight + timedelta(seconds=6), number=9,
                                  subject=elsewhere, course="backend_copy")
                assert await ActivityStorage(session).insert(foreign) == "inserted"
            # A pending projection for an unrelated course and learner does not
            # consume this course's subject budget or block its snapshot.
            with monkeypatch.context() as scoped:
                scoped.setattr(study_module, "MAX_MEMBERSHIPS", 3)
                observed = await read_course_study_time(reports, grant(), aggregate)
                assert observed.daily[-1].with_subscription is not None

            # Check the read lock is still held during the final privacy check.
            calls = 0
            original_guard = reader.transaction_guard

            async def guard(_session):
                nonlocal calls
                calls += 1
                if calls == 4:
                    async with migrate.transaction() as session:
                        locked = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                                                        {"key": subject_lock_key(paid)})).scalar_one()
                        assert locked is False

            reader.transaction_guard = guard
            assert (await read_course_study_time(reports, grant(), aggregate)).daily[-1].with_subscription is not None
            assert calls == 4
            reader.transaction_guard = original_guard

            async with migrate.transaction() as session:
                await session.execute(text("""INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id)
                    VALUES (:subject,1,true,:control)"""), {"subject": paid, "control": uuid4()})
            with pytest.raises(ProjectionUnavailable):
                await read_course_study_time(reports, grant(), aggregate)
            clean_discovery = await read_course_study_time(reports, grant(), discovery)
            assert clean_discovery.subject_digests == sorted([digest(free), digest(staff)])
            clean = await read_course_study_time(reports, grant(), aggregate.model_copy(update={"active_subscriptions": []}))
            assert clean.daily[-1].with_subscription is None and clean.daily[-1].without_subscription.learners == 1

            calls = 0

            async def add_candidate_before_snapshot(_session):
                nonlocal calls
                calls += 1
                if calls == 3:
                    async with writer.transaction() as session:
                        event = g.event(midnight + timedelta(seconds=2), midnight + timedelta(seconds=6),
                                        number=10, subject=elsewhere)
                        assert await ActivityStorage(session).insert(event) == "inserted"

            reader.transaction_guard = add_candidate_before_snapshot
            with pytest.raises(ProjectionUnavailable):
                await read_course_study_time(reports, grant(), discovery)
            assert calls == 3
            reader.transaction_guard = original_guard
            async with migrate.transaction() as session:
                await session.execute(text("""INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id)
                    VALUES (:subject,1,true,:control)"""), {"subject": actor, "control": uuid4()})
            with pytest.raises(AuthorizationError):
                await read_course_study_time(reports, grant(), discovery)
        finally:
            for db in (writer, worker, reader, migrate):
                await db.close()

    asyncio.run(run())
