from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from pymentor_analytics.contracts.v2 import BrowserEvent, canonical_hash
from pymentor_analytics.modules.collector.application.admission import BrowserIdentity
from pymentor_analytics.modules.collector.application.metadata import (
    MetadataRejected, build_record, make_record, validate_new_record,
)

NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
IDENTITY = BrowserIdentity(UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), "browser:verified", source="forged")
FIXTURE = Path(__file__).parents[2] / "contracts/fixtures/browser-batch.json"


def event(when):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["events"][0]
    stamp = when.isoformat()
    data.update(started_at=stamp, ended_at=stamp, last_action_at=stamp)
    return BrowserEvent.model_validate_json(json.dumps(data))


def test_server_identity_and_only_allowlisted_metadata():
    e = event(NOW.astimezone(timezone(timedelta(hours=3))))
    record = make_record(IDENTITY, e, clock=lambda: NOW.astimezone(timezone(timedelta(hours=-5))))
    assert record.subject_ref == IDENTITY.subject_ref
    assert record.producer_namespace == "browser:verified" and record.source == "browser"
    assert record.received_at == NOW and record.received_at.tzinfo == timezone.utc
    assert record.event.ended_at.utcoffset() == timedelta(hours=3)
    assert record.event is e
    assert set(record.model_dump()) == {"producer_namespace", "subject_ref", "source", "event", "received_at"}
    # Browser data cannot smuggle proxy/header/body identity fields into the DTO.
    wire = e.model_dump(mode="json")
    for field in ("user_id", "source", "x_forwarded_for", "referer", "cookie", "user_agent"):
        with pytest.raises(ValidationError):
            BrowserEvent.model_validate_json(json.dumps({**wire, field: "forged"}))
    with pytest.raises(ValueError): build_record(IDENTITY, e, clock=lambda: NOW.replace(tzinfo=None))


@pytest.mark.parametrize("offset,reason", [
    (timedelta(seconds=120), None), (timedelta(seconds=120, microseconds=1), "future_clock"),
    (-timedelta(days=7), None), (-timedelta(days=7, microseconds=1), "too_late"),
    (-timedelta(days=3), None), (-timedelta(seconds=10), None),
])
def test_clock_bounds_offline_and_out_of_order(offset, reason):
    if reason:
        with pytest.raises(MetadataRejected) as exc:
            make_record(IDENTITY, event(NOW + offset), clock=lambda: NOW)
        assert exc.value.reason == reason
    else:
        assert make_record(IDENTITY, event(NOW + offset), clock=lambda: NOW).event.ended_at == NOW + offset


def test_retry_hash_is_stable_but_new_record_policy_changes():
    e = event(NOW)
    first = make_record(IDENTITY, e, clock=lambda: NOW)
    retry = build_record(IDENTITY, e, clock=lambda: NOW + timedelta(days=8))
    assert canonical_hash(first) == canonical_hash(retry)
    with pytest.raises(MetadataRejected, match="too_late"):
        validate_new_record(retry)
    # A real durable store must resolve the matching hash before the latter check.


def test_cli_disables_access_log(monkeypatch):
    from pymentor_analytics.app import cli
    from types import SimpleNamespace
    captured = {}
    monkeypatch.setattr(cli.sys, "argv", ["analytics", "collector"])
    monkeypatch.setattr(cli, "load_settings", lambda role: SimpleNamespace(shutdown_timeout_seconds=5, real_collection=False))
    monkeypatch.setattr(cli, "create_app", lambda *args: object())
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))
    assert cli.main() == 0
    assert captured["access_log"] is False
