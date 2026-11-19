"""Self calendar from positive measured time; source coverage is unknown."""
import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

MOSCOW = ZoneInfo("Europe/Moscow")


def _query(start: date, end: date, course_id: str | None = None) -> ActivityQuery:
    def midnight(day):
        return datetime.combine(day, time.min, MOSCOW).astimezone(timezone.utc)
    return ActivityQuery(from_time=midnight(start), to_time=midnight(end),
                         course_id=course_id, subject_ref=None)


async def read_measured_calendar(store, grant, year: int, month: int, *, now: datetime, course_id: str | None = None):
    start = date(year, month, 1)
    end = start + timedelta(days=calendar.monthrange(year, month)[1])
    today = now.astimezone(MOSCOW).date()
    monthly = await store.read_activity(grant, _query(start, end, course_id))
    recent = await store.read_activity(grant, _query(today - timedelta(days=30), today + timedelta(days=1), course_id))
    if monthly.get("status") == "unavailable" or recent.get("status") == "unavailable":
        raise IntakeUnavailable()
    days = sorted(row["date"] for row in monthly["daily"] if row["active_ms"] > 0)
    active = {row["date"] for row in recent["daily"] if row["active_ms"] > 0}
    cursor = today if today in active else today - timedelta(days=1)
    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    # An absent observed day is not proof of inactivity or source freshness.
    times = (monthly["as_of"], recent["as_of"])
    result = {"days": days, "current_streak": streak or None, "streak_is_lower_bound": bool(streak),
              "coverage": "unknown", "as_of": min(times) if all(times) else None}
    if course_id is not None:
        result["course_id"] = course_id
    return result
