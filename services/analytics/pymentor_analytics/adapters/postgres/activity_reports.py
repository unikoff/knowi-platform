"""Bounded per-subject snapshot; not an HTTP endpoint or coverage proof."""
from datetime import timedelta
import json
import time
from sqlalchemy import text
from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema
from pymentor_analytics.contracts.auth_v1 import AuthorizationError
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.modules.analytics.activity.calculator import calculate
from pymentor_analytics.modules.analytics.reports.auth import authorize_report
from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope

MAX_FACTS = 10000
MAX_SUBJECTS = 100

class ReportLimitExceeded(Exception):
    """No partial result is returned when a read exceeds the bound."""

class PostgresActivityReport:
    def __init__(self, database, fence):
        self.database, self.fence = database, fence

    async def read_subject(self, grant, query: ActivityQuery):
        if query.course_id is None or query.subject_ref is None:
            raise ValueError("a single authorized subject and course are required")
        await authorize_report(grant, course_id=query.course_id, subject_ref=query.subject_ref, fence=self.fence)
        if self.database.transaction_guard is None:
            raise PrivacyUnavailable()
        async with self.database.transaction() as session:
            source_filter = " AND source_kind='browser_v2'" if await has_core_schema(session) else ""
            identities = {grant.sub, query.subject_ref}
            for key in sorted({subject_lock_key(identity) for identity in identities}):
                await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
            for identity in identities:
                deleted = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), {"subject": identity})).scalar_one_or_none()
                if deleted is True:
                    raise AuthorizationError()
            if time.time() >= grant.expires_at:
                raise AuthorizationError()
            now = (await session.execute(text("SELECT date_trunc('milliseconds', clock_timestamp())"))).scalar_one()
            # Segment length is <=60 seconds; include all competing courses.
            rows = (await session.execute(text("""SELECT accepted_event FROM analytics.activity_facts
                WHERE subject_ref=:subject AND started_at >= :lower AND started_at < :upper
                  AND received_at <= :as_of""" + source_filter + """ ORDER BY started_at, producer_namespace, event_id
                LIMIT :limit"""), {"subject": query.subject_ref, "lower": query.from_time - timedelta(seconds=60),
                                   "upper": query.to_time, "as_of": now, "limit": MAX_FACTS + 1})).scalars().all()
            if len(rows) > MAX_FACTS:
                raise ReportLimitExceeded()
            records = [AcceptedEvent.model_validate_json(json.dumps(value)) for value in rows]
            result = calculate(records, start=query.from_time, end=query.to_time, as_of=now,
                               subject_ref=query.subject_ref, course_id=query.course_id, lesson_id=query.lesson_id)
            if time.time() >= grant.expires_at:
                raise AuthorizationError()
            # Transaction guard is rechecked at exit before the result is released.
        if time.time() >= grant.expires_at:
            raise AuthorizationError()
        return {"as_of": now, "quality": "unknown", "lag_seconds": None, **result}

    async def read_activity(self, grant, query: ActivityQuery, *, include_breakdown: bool = False):
        """Bounded, scoped aggregate. Coverage remains unknown, including an empty snapshot."""
        scope = resolve_scope(grant, query)
        if self.database.transaction_guard is None:
            raise PrivacyUnavailable()
        async with self.database.transaction() as session:
            source_filter = " AND source_kind='browser_v2'" if await has_core_schema(session) else ""
            now = (await session.execute(text("SELECT date_trunc('milliseconds', clock_timestamp())"))).scalar_one()
            params = {"lower": query.from_time - timedelta(seconds=60), "upper": query.to_time,
                      "as_of": now, "limit": MAX_SUBJECTS + 1}
            restrictions = []
            if scope.subjects is not None:
                restrictions.append("subject_ref=ANY(CAST(:subjects AS uuid[]))")
                params["subjects"] = list(scope.subjects)
            if scope.courses is not None:
                restrictions.append("course_id=ANY(CAST(:courses AS varchar[]))")
                params["courses"] = list(scope.courses)
            where = " AND ".join(restrictions)
            where = " AND " + where if where else ""
            # No subject list escapes this transaction; every candidate is checked
            # against the deletion fence after locks, before its facts are read.
            candidates = (await session.execute(text("""SELECT DISTINCT subject_ref FROM analytics.activity_facts
                WHERE started_at >= :lower AND started_at < :upper AND received_at <= :as_of""" + source_filter + where +
                " ORDER BY subject_ref LIMIT :limit"), params)).scalars().all()
            if len(candidates) > MAX_SUBJECTS:
                raise ReportLimitExceeded()
            identities = set(candidates) | {scope.actor}
            if scope.selected_subject is not None:
                identities.add(scope.selected_subject)
            dead = set()
            for key in sorted({subject_lock_key(identity) for identity in identities}):
                await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
            for identity in identities:
                deleted = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                                 {"subject": identity})).scalar_one_or_none()
                if deleted is True:
                    dead.add(identity)
            if scope.actor in dead or scope.selected_subject in dead:
                raise AuthorizationError()
            subjects = [subject for subject in candidates if subject not in dead]
            if time.time() >= grant.expires_at:
                raise AuthorizationError()
            if subjects:
                rows = (await session.execute(text("""SELECT accepted_event FROM analytics.activity_facts
                    WHERE subject_ref=ANY(CAST(:subjects AS uuid[])) AND started_at >= :lower
                      AND started_at < :upper AND received_at <= :as_of""" + source_filter + """
                    ORDER BY subject_ref, started_at, producer_namespace, event_id LIMIT :limit"""),
                    {"subjects": subjects, "lower": params["lower"], "upper": params["upper"],
                     "as_of": now, "limit": MAX_FACTS + 1})).scalars().all()
            else:
                rows = []
            if len(rows) > MAX_FACTS:
                raise ReportLimitExceeded()
            records = [AcceptedEvent.model_validate_json(json.dumps(value)) for value in rows]
            result = calculate(records, start=query.from_time, end=query.to_time, as_of=now,
                               allowed_courses=scope.courses, lesson_id=query.lesson_id,
                               include_breakdown=include_breakdown)
            if time.time() >= grant.expires_at:
                raise AuthorizationError()
        if time.time() >= grant.expires_at:
            raise AuthorizationError()
        # Do not expose snapshot timestamps inferred from other courses on empty scopes.
        return {"as_of": now if result["totals"]["unique_visitors"] else None,
                "quality": "unknown", "lag_seconds": None, "course_id": scope.course_id, **result}
