"""Validators change with the observation, subject, course and database incarnation."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymentor_analytics.adapters.postgres.activity_history_revisions import encode_history_generation, encode_history_revisions


def test_token_scope_incarnation_and_unknown_dates():
    now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    row = {"date": (now-timedelta(days=5)).date(), "unique_visitors": 1,
           "active_ms": 10000, "theory_ms": 10000, "practice_ms": 0}
    args = dict(subject=uuid4(), course="backend", run_id=uuid4(), started=now-timedelta(days=1),
                db_oid=123, db_name="analytics_test", now=now)
    def encode(rows, **changes):
        return encode_history_revisions(rows, **{**args, **changes})
    result = encode([row])
    assert len(result) == 28 and sum(item["token"] is not None for item in result) == 1
    assert encode([row]) == result
    for change in ({"subject": uuid4()}, {"course": "another"}, {"run_id": uuid4()},
                   {"db_oid": 124}, {"db_name": "restored"}, {"started": now}):
        assert encode([row], **change) != result
    assert encode([{**row, "active_ms": 11000, "theory_ms": 11000}]) != result
    assert all(item["token"] is None for item in encode([]))
    identity = {key: value for key, value in args.items() if key != "now"}
    generation = encode_history_generation(**identity)
    assert len(generation) == 64 and all(char in "0123456789abcdef" for char in generation)
    assert encode_history_generation(**{**identity, "db_oid": 124}) != generation
