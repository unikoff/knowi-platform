"""Pure learner statistics derived from a trusted snapshot."""
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from pymentor_analytics.contracts.learner_report_v1 import Coverage, Day, LearnerReport, LearnerSnapshot, Period, Scope, Summary

MOSCOW = ZoneInfo("Europe/Moscow")


def build_report(snapshot: LearnerSnapshot) -> LearnerReport:
    today = snapshot.snapshot_at.astimezone(MOSCOW).date()
    start = today - timedelta(days=snapshot.period_days - 1)
    visits = {day for day in snapshot.activity_dates if day <= today}
    streak = None
    if snapshot.activity_complete:
        streak = 0
        cursor = today if today in visits else today - timedelta(days=1)
        while cursor in visits:
            streak += 1
            cursor -= timedelta(days=1)
    # Earliest trusted completion wins if duplicate facts disagree on their date.
    completions = {}
    for item in snapshot.lesson_completions:
        if item.completed_at > snapshot.snapshot_at or (snapshot.course_id is not None and item.course_id != snapshot.course_id):
            continue
        key = (item.course_id, item.lesson_id)
        if key not in completions or item.completed_at < completions[key]:
            completions[key] = item.completed_at
    counts = Counter(value.astimezone(MOSCOW).date() for value in completions.values())
    days = [Day(date=start + timedelta(days=offset), visited=start + timedelta(days=offset) in visits,
                completed_lessons=counts[start + timedelta(days=offset)])
            for offset in range(snapshot.period_days)]
    return LearnerReport(as_of=snapshot.snapshot_at,
        period=Period(days=snapshot.period_days, start=start, end=today),
        scope=Scope(course_id=snapshot.course_id),
        coverage=Coverage(visits="legacy_global" if snapshot.activity_complete else "partial"),
        summary=Summary(active_days_total=len(visits) if snapshot.activity_complete else None,
                        current_streak=streak, active_days_period=sum(d.visited for d in days),
                        completed_lessons_period=sum(d.completed_lessons for d in days)), days=days)
