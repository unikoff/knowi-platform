"""Conditional daily values for imported Core dates, separate from measurements."""
from datetime import date


def conditional_history_days(dates):
    """One nominal pair per date; duplicates cannot increase reported history."""
    unique = sorted({date.fromisoformat(value) for value in dates})
    return [{"date": day.isoformat(), "theory_ms": 3_600_000,
             "practice_ms": 3_600_000, "provenance": "core_legacy_conditional"}
            for day in unique]
