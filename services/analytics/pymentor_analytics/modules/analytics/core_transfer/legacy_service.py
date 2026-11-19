"""G1-scoped legacy projection; unknown coverage is never a cutover proof."""
from calendar import monthrange
from datetime import date, datetime, timezone
import time
from uuid import UUID

from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.modules.analytics.core_transfer.compatibility import MOSCOW, admin_activity, admin_dashboard, calendar, summary


class LegacyService:
    def __init__(self, store, namespace, roster, *, require_coverage=False):
        self.store, self.namespace, self.roster = store, namespace, roster
        self.require_coverage = require_coverage

    @staticmethod
    def _complete(data, coverage):
        return {"coverage": "complete", "data": data,
                "coverage_through_revision": coverage["coverage_through_revision"],
                "source_snapshot_at": coverage["source_snapshot_at"], "source_hwm": None, "freshness": "unknown"}

    def _browser_metadata(self, envelope, result, position):
        if getattr(self.store, "browser_run_id", None) is not None:
            envelope.update(browser_quality="unknown", browser_as_of=result[position])
        return envelope

    def _authorize(self, grant, role):
        valid_role = ((role == "self" and grant.role in {"user", "leader", "admin"})
                      or (role == "admin" and grant.role == "admin")) if isinstance(grant, VerifiedGrant) else False
        if (not isinstance(grant, VerifiedGrant) or grant.scope != "report:read" or not valid_role
                or time.time() >= grant.expires_at or (grant.role == "admin" and not grant.global_read)):
            raise AuthorizationError()
        if getattr(self.store.database, "transaction_guard", None) is None:
            raise PrivacyUnavailable()

    async def read_history(self, grant, start, end, *, deadline):
        self._authorize(grant, "self")
        if grant.role != "user" or not grant.course_ids or grant.subject_refs or grant.global_read or grant.legacy_admin_roster_v1 is not None:
            raise AuthorizationError()
        if type(start) is not date or type(end) is not date or not 0 <= (end - start).days <= 30:
            raise ValueError("invalid history range")
        result = await self.store.read_history(self.namespace, grant.sub, start, end, deadline=deadline)
        self._authorize(grant, "self")
        return result

    async def read_calendar(self, grant, year, month, *, deadline):
        self._authorize(grant, "self")
        if type(year) is not int or type(month) is not int or not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise ValueError("invalid calendar range")
        today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
        start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        kwargs = {"deadline": deadline, "visit_through": max(today, end)}
        if self.require_coverage:
            kwargs["require_coverage"] = True
        result = await self.store.read_subject(self.namespace, grant.sub, today, **kwargs)
        visits = result[0]
        self._authorize(grant, "self")
        data = calendar(visits, start, end)
        envelope = self._complete(data, result[2]) if self.require_coverage else {"coverage": "unknown", "data": data}
        return self._browser_metadata(envelope, result, 3)

    async def read_summary(self, grant, *, deadline):
        self._authorize(grant, "self")
        today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
        kwargs = {"deadline": deadline}
        if self.require_coverage:
            kwargs["require_coverage"] = True
        result = await self.store.read_subject(self.namespace, grant.sub, today, **kwargs)
        visits, completions = result[:2]
        self._authorize(grant, "self")
        data = summary(today, visits, completions)
        envelope = self._complete(data, result[2]) if self.require_coverage else {"coverage": "unknown", "data": data}
        return self._browser_metadata(envelope, result, 3)

    async def read_admin_profile(self, grant, subject, year, month, *, deadline):
        """One fenced snapshot for a server-authorized target and all profile fields."""
        self._authorize(grant, "admin")
        if (not isinstance(subject, UUID) or type(year) is not int or type(month) is not int
                or not 2000 <= year <= 2100 or not 1 <= month <= 12):
            raise ValueError("invalid profile target or calendar range")
        today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
        start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        result = await self.store.read_subject(self.namespace, subject, today, deadline=deadline,
            actor=grant.sub, include_all_visits=True, visit_through=max(today, end), require_coverage=True)
        self._authorize(grant, "admin")
        visits, completions, coverage = result[:3]
        days = sorted({v["activity_date"] for v in visits})
        selected = [day for day in days if start <= date.fromisoformat(day) <= end]
        data = {"activity_days_count": len(selected), "activity_days": selected,
                "total_activity_days": len(days), "last_activity_date": days[-1] if days else None,
                "activity_summary": summary(today, visits, completions)}
        return self._browser_metadata(self._complete(data, coverage), result, 3)

    async def read_admin(self, grant, *, deadline):
        self._authorize(grant, "admin")
        if self.require_coverage:
            members = grant.legacy_admin_roster_v1
            if members is None or len(members) > 80 or len({member[0] for member in members}) != len(members):
                raise PrivacyUnavailable()
            if not any(member[0] == grant.sub and member[2] is False for member in members):
                raise PrivacyUnavailable()
            roster = {"subjects": tuple(member[0] for member in members),
                      "students": frozenset(str(member[0]) for member in members if member[2]),
                      "revisions": {member[0]: member[1] for member in members}}
        else:
            roster = self.roster
            if roster is None or time.time() >= roster["expires_at"]:
                raise PrivacyUnavailable()
        today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
        kwargs = {"actor": grant.sub, "deadline": deadline}
        if self.require_coverage:
            kwargs["required_revisions"] = roster["revisions"]
        result = await self.store.read_admin(self.namespace, roster["subjects"], today, **kwargs)
        visits = result[0] if self.require_coverage or getattr(self.store, "browser_run_id", None) is not None else result
        self._authorize(grant, "admin")
        if not self.require_coverage and time.time() >= roster["expires_at"]:
            raise PrivacyUnavailable()
        data = admin_activity(today, visits, roster["students"])
        data["dashboard_activity"] = admin_dashboard(today, visits, roster["students"])
        if not self.require_coverage:
            return self._browser_metadata({"coverage": "unknown", "data": data}, result, 2)
        vector = [{**item, "student": item["subject_ref"] in roster["students"]} for item in result[1]]
        envelope = {"coverage": "complete", "data": data, "coverage_subjects": vector,
                    "source_hwm": None, "freshness": "unknown"}
        return self._browser_metadata(envelope, result, 2)

    async def read_admin_completions(self, grant, at, *, deadline):
        self._authorize(grant, "admin")
        members = grant.legacy_admin_roster_v1
        if grant.course_ids or grant.subject_refs or members is None or not self.require_coverage:
            raise AuthorizationError()
        if (len(members) > 80 or len({member[0] for member in members}) != len(members)
                or not any(subject == grant.sub and not student for subject, _, student in members)):
            raise PrivacyUnavailable()
        if abs((datetime.now(timezone.utc) - at).total_seconds()) > 60:
            raise ValueError("invalid completion report time")
        roster = {subject: revision for subject, revision, student in members if student}
        result = await self.store.read_completion_summary(self.namespace, roster, grant.sub, at, deadline=deadline)
        self._authorize(grant, "admin")
        result["weekly_by_subject"] = {str(subject): count for subject, count in result["weekly_by_subject"].items()}
        return result

    async def read_course_completions(self, grant, course, at, year, month, *, deadline):
        self._authorize(grant, "self")
        scoped = grant.course_completion_roster_v1
        if (not self.require_coverage or grant.role not in {"leader", "admin"} or scoped is None
                or scoped[:3] != (course, year, month)):
            raise AuthorizationError()
        if abs((datetime.now(timezone.utc) - at).total_seconds()) > 60:
            raise ValueError("invalid completion report time")
        window = None
        if year is not None:
            start = datetime(year, month, 1, tzinfo=MOSCOW)
            end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=MOSCOW)
            window = start, end
        roster = {subject: revision for subject, revision, _ in scoped[3]}
        students = {subject for subject, _, student in scoped[3] if student}
        result = await self.store.read_completion_summary(self.namespace, roster, grant.sub, at, deadline=deadline,
            course_id=course, dashboard_subjects=students, monthly_window=window)
        self._authorize(grant, "self")
        result["weekly_by_subject"] = {str(subject): count for subject, count in result["weekly_by_subject"].items()}
        return result
