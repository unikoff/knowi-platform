"""Pure legacy read semantics; dates are client dates, completions use Moscow."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def calendar(visits, start, end):
    selected = sorted((v for v in visits if start <= date.fromisoformat(v["activity_date"]) <= end),
                      key=lambda v: v["activity_date"])
    return {"days": [v["activity_date"] for v in selected], "visits": selected}


def summary(today, visits, completions):
    dates = {date.fromisoformat(v["activity_date"]) for v in visits if date.fromisoformat(v["activity_date"]) <= today}
    visited = today in dates
    current = today if visited else today - timedelta(days=1)
    streak = 0
    while current in dates:
        streak += 1
        current -= timedelta(days=1)
    days = [today - timedelta(days=6 - offset) for offset in range(7)]
    counts = {day: 0 for day in days}
    for value in completions:
        if value["completed_at"] is not None:
            day = datetime.fromisoformat(value["completed_at"]).astimezone(MOSCOW).date()
            if day in counts:
                counts[day] += 1
    return {"current_streak": streak, "visited_today": visited, "total_active_days": len(dates),
            "weekly_activity": [{"activity_date": day.isoformat(), "active": day in dates} for day in days],
            "weekly_lessons_completed": [{"activity_date": day.isoformat(), "lessons_completed": counts[day]} for day in days]}


def admin_activity(today, visits, student_refs):
    """Input must already be authorized and complete; student refs come from Core."""
    start = today - timedelta(days=6)
    today_subjects = {v["subject_ref"] for v in visits if date.fromisoformat(v["activity_date"]) == today}
    students = {v["subject_ref"] for v in visits if v["subject_ref"] in student_refs
                and start <= date.fromisoformat(v["activity_date"]) <= today}
    return {"platform_activity_today": {"date": today.isoformat(), "unique_users": len(today_subjects),
              "coverage_since": None, "partial_day": True},
            "platform_course_activity_today": {"date": today.isoformat(), "unique_users": None,
              "coverage_since": None, "partial_day": False},
            "legacy_dashboard_active_students_7d": len(students)}


def admin_dashboard(today, visits, student_refs):
    active = {v["subject_ref"] for v in visits if v["subject_ref"] in student_refs
              and today - timedelta(days=6) <= date.fromisoformat(v["activity_date"]) <= today}
    daily = []
    for offset in range(29, -1, -1):
        day = today - timedelta(days=offset)
        subjects = {v["subject_ref"] for v in visits if v["subject_ref"] in student_refs
                    and date.fromisoformat(v["activity_date"]) == day}
        daily.append({"date": day.isoformat(), "active_students": len(subjects)})
    return {"active_students_7d": len(active), "inactive_students_7d": len(student_refs) - len(active),
            "daily_active_students": daily}
