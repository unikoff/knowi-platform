import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded
from pymentor_analytics.adapters.postgres.course_visitors import discover_course_visitor_candidates, read_course_visitor_report
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import AuthorizationError
from pymentor_analytics.contracts.course_visitors import CourseVisitorAggregation, CourseVisitorDiscovery, CourseVisitorGrant

spec = importlib.util.spec_from_file_location("visit_facts", Path(__file__).parents[1] / "reference/test_activity_v1.py")
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


def test_daily_distinct_subscription_cohorts_filter_course_moscow_and_fences(activity_projection_target):
    _, _, urls, _, _ = activity_projection_target

    async def check():
        write, read, privacy = [Database(Settings(database_url=urls[role])) for role in ("collector", "query", "privacy")]
        guard_calls = []

        async def guard(_session):
            guard_calls.append(True)

        read.transaction_guard = guard
        actor, active, inactive, elsewhere = [uuid4() for _ in range(4)]
        nonce = "e" * 32
        stamp = int(time.time())
        cut = datetime(2026, 11, 18, 21, tzinfo=timezone.utc)
        tally = CourseVisitorAggregation(
            at=cut + timedelta(minutes=5), nonce=nonce, operation="aggregate",
            active_subscriptions=[sha256((nonce + str(active)).encode()).hexdigest()],
            inactive_subscriptions=[sha256((nonce + str(inactive)).encode()).hexdigest()],
        )
        grant = CourseVisitorGrant(grant_version=1, sub=actor, iss="core", aud="analytics-course-visitors",
                                  iat=stamp, nbf=stamp, exp=stamp + 15, course_id="backend", body_sha256="0" * 64)
        events = [
            facts.event(cut - timedelta(seconds=5), cut + timedelta(seconds=5), number=1, subject=active),
            facts.event(cut, cut, opened=True, number=2, subject=active),
            facts.event(cut, cut, opened=True, number=3, subject=inactive),
            facts.event(cut, cut, opened=True, number=4, subject=elsewhere, course="other_course"),
        ]

        async def fence(db, subject):
            async with db.transaction() as session:
                await session.execute(text("""INSERT INTO analytics.subject_fences (subject_ref, epoch, deleted, control_id)
                    VALUES (:subject, 1, TRUE, :control)"""), {"subject": subject, "control": uuid4()})

        try:
            async with write.transaction() as session:
                for item in events:
                    assert await ActivityStorage(session).insert(item) == "inserted"
            discovery = CourseVisitorDiscovery(at=tally.at, nonce=nonce, operation="discover")
            candidates = await discover_course_visitor_candidates(read, grant, discovery)
            assert set(candidates.subject_digests) == set(tally.active_subscriptions + tally.inactive_subscriptions)
            result = await read_course_visitor_report(read, grant, tally)
            assert len(guard_calls) == 6 and len(result.daily) == 30
            assert result.daily[-3].with_subscription is None
            assert result.daily[-2].date.isoformat() == "2026-11-18"
            assert result.daily[-2].with_subscription == 1 and result.daily[-2].without_subscription is None
            assert result.daily[-1].date.isoformat() == "2026-11-19"
            assert result.daily[-1].with_subscription == result.daily[-1].without_subscription == 1
            no_match = tally.model_copy(update={"inactive_subscriptions": []})
            with pytest.raises(ReportLimitExceeded):
                await read_course_visitor_report(read, grant, no_match)
            await fence(privacy, inactive)
            with pytest.raises(ReportLimitExceeded):
                await read_course_visitor_report(read, grant, tally)
            candidates = await discover_course_visitor_candidates(read, grant, discovery)
            assert candidates.subject_digests == tally.active_subscriptions
            report = await read_course_visitor_report(read, grant, no_match)
            assert report.daily[-1].without_subscription is None
            await fence(privacy, actor)
            with pytest.raises(AuthorizationError):
                await read_course_visitor_report(read, grant, tally)
        finally:
            for db in (write, read, privacy):
                await db.close()

    asyncio.run(check())
