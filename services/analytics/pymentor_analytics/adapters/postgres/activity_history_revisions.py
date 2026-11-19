"""Scope-bound cache validators for observed daily values in a guarded snapshot."""
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def whole_days(query):
    return (query.from_time.astimezone(MOSCOW).time() == time.min
            and query.to_time.astimezone(MOSCOW).time() == time.min)


def with_incarnation(statement):
    # All metadata and report values come from the same PostgreSQL statement.
    return f"""WITH revision_report AS MATERIALIZED ({statement})
        SELECT revision_report.*, pg_postmaster_start_time() AS revision_started,
          (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS revision_db_oid,
          current_database() AS revision_db_name, statement_timestamp() AS revision_now
        FROM revision_report"""


def encode_history_generation(*, subject, course, run_id, started, db_oid, db_name):
    if (not isinstance(subject, UUID) or not isinstance(run_id, UUID) or not isinstance(course, str)
            or not isinstance(started, datetime) or started.utcoffset() is None
            or type(db_oid) is not int or db_oid <= 0 or not isinstance(db_name, str) or not db_name):
        raise ValueError("invalid database incarnation")
    payload = ["history-generation-v1", str(subject), course, str(run_id), started.isoformat(), db_oid, db_name]
    return sha256(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def encode_history_revisions(daily, *, subject, course, run_id, started, db_oid, db_name, now):
    encode_history_generation(subject=subject, course=course, run_id=run_id, started=started,
                              db_oid=db_oid, db_name=db_name)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("invalid snapshot time")
    by_day = {row["date"]: row for row in daily}
    if len(by_day) != len(daily) or any(not isinstance(day, date) for day in by_day):
        raise ValueError("invalid daily values")
    today = now.astimezone(MOSCOW).date()
    result = []
    for offset in range(29, 1, -1):
        day = today - timedelta(days=offset)
        row = by_day.get(day)
        token = None
        if row is not None:
            payload = ["history-revision-v1", str(subject), course, str(run_id),
                       started.isoformat(), db_oid, db_name, day.isoformat(),
                       row["unique_visitors"], row["active_ms"], row["theory_ms"], row["practice_ms"]]
            token = sha256(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        result.append({"date": day.isoformat(), "token": token})
    return result
