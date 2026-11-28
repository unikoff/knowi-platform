"""Shared contract vectors authored independently of the production tracker and store."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from pymentor_analytics.contracts import windows_v1 as w
from pymentor_analytics.contracts.metadata_v1 import validate_new_record
from pymentor_analytics.contracts.v2 import AcceptedEvent


FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/fixtures/windows-v1/scenarios.json").read_text(encoding="utf-8")
)
CASES = FIXTURE["cases"]
GRANTS = {
    name: w.WindowGrantClaims.model_validate_json(json.dumps(body))
    for name, body in FIXTURE["grants"].items()
}
CATALOG = {
    name: w.PublishedCourse(info["version"], frozenset(info["lesson_ids"]))
    for name, info in FIXTURE["catalog"].items()
}
CLOCK = datetime.fromisoformat(FIXTURE["clock"])


def snapshot(body):
    return w.WindowSnapshot.model_validate_json(json.dumps(body))


def stats(rows):
    return tuple(w.WindowStat.model_validate(row) for row in rows)


@pytest.mark.parametrize("reverse", (False, True))
def test_602_a_whole_window_canonical_hash(reverse):
    case = CASES["602-А"]
    raw = case["batch"]
    if reverse:
        raw = {**raw, "windows": [{**raw["windows"][0], "stats": raw["windows"][0]["stats"][::-1]}]}
    (window,) = w.WindowBatch.model_validate_json(json.dumps(raw)).windows
    expected = case["expected"]
    assert w.authorize_snapshot(window, GRANTS["A"], CATALOG) is None
    assert w.validate_window_age(window, CLOCK) is None
    result = w.receive_snapshot(w.WindowState(), window)
    assert (result.status, result.code) == ("committed", "committed")
    assert result.state.confirmed.stats == stats(expected["stats"])
    assert result.state.confirmed.revision == expected["revision"]
    assert sum(row.active_ms for row in result.state.confirmed.stats) == expected["total_ms"]
    assert w.snapshot_digest(window) == expected["digest"]
    ack = w.build_ack(0, window, result)
    assert [ack.activity_date.isoformat(), ack.slot_no] == expected["key"]
    assert w.WindowBatchAck(protocol_version=1, items=(ack,)).items[0].confirmed == result.state.confirmed


@pytest.mark.parametrize("index", (0, 1))
def test_602_b_quarter_and_midnight(index):
    case = CASES["602-Б"]
    start, end = (datetime.fromisoformat(value) for value in case["intervals"][index])
    observed = w.split_interval(start, end)
    assert [
        [coordinate.activity_date.isoformat(), coordinate.slot_no, elapsed]
        for coordinate, elapsed in observed
    ] == case["expected"][2 * index:2 * index + 2]
    assert sum(elapsed for _, elapsed in observed) == (end - start) // timedelta(milliseconds=1)


def test_602_b_half_open_and_millisecond_rounding():
    start = datetime.fromisoformat("2026-11-27T15:14:59.999900+03:00")
    end = datetime.fromisoformat("2026-11-27T15:15:00.000100+03:00")
    assert [(key.slot_no, elapsed) for key, elapsed in w.split_interval(start, end)] == [(61, 1)]
    assert w.split_interval(end, end) == ()


def test_602_c_sequence_duplicate_and_second_writer():
    case = CASES["602-В"]
    state = w.WindowState()
    for step in case["steps"]:
        value = snapshot(step["snapshot"])
        assert w.authorize_snapshot(value, GRANTS[step["grant"]], CATALOG) is None
        outcome = w.receive_snapshot(state, value)
        assert [outcome.status, outcome.code, outcome.state.confirmed.revision] == step["expected"]
        if outcome.status not in {"committed", "pending"}:
            assert outcome.state is state
        state = outcome.state
    expected = case["expected"]
    assert str(state.confirmed.writer_id) == expected["writer_id"]
    assert state.confirmed.server_epoch == expected["server_epoch"]
    assert state.confirmed.seq == expected["seq"]
    assert state.confirmed.revision == expected["revision"]
    assert sum(row.active_ms for row in state.confirmed.stats) == expected["total_ms"]
    assert state.state == expected["state"]
    assert state.coverage_reasons == ("writer_conflict",)


@pytest.mark.parametrize("index", range(8))
def test_602_d_atomic_authorization(index):
    case = CASES["602-Г"]
    check = case["checks"][index]
    initial = w.WindowState()
    raw = check["snapshot"]
    try:
        value = snapshot(raw)
    except ValidationError as error:
        reason = w.snapshot_rejection(error, raw)
    else:
        reason = w.authorize_snapshot(value, GRANTS[check["grant"]], CATALOG)
    assert reason == check["expected"]
    if reason is not None:
        assert initial.confirmed is None
        return
    result = w.receive_snapshot(initial, value)
    assert result.state.confirmed.stats == stats(case["expected_allowed"]["stats"])
    assert result.state.confirmed.revision == case["expected_allowed"]["revision"]


def test_602_e_late_v2_reopens_previously_verified_window():
    case = CASES["602-Д"]
    expected = case["expected"]
    value = snapshot(case["snapshot"])
    state = w.receive_snapshot(w.WindowState(), value).state
    baseline_facts = tuple(AcceptedEvent.model_validate_json(json.dumps(entry)) for entry in case["v2_baseline_facts"])
    assert sum(record.event.active_ms for record in baseline_facts) == expected["unchanged_ms"]
    covered = w.ReconcileDecision(state.source_generation, state.change_generation, value.stats)
    verified = w.apply_reconciliation(state, covered, rebase_at=CLOCK)
    assert verified.state == "verified"
    late = AcceptedEvent.model_validate_json(json.dumps(case["v2_late_fact"]))
    validate_new_record(late)
    assert (late.received_at - late.event.ended_at).days == 5  # sixth day, still admissible
    assert w.late_deadline(value).isoformat() == expected["deadline"]
    assert expected["unchanged_ms"] + late.event.active_ms == expected["expected_after_late_ms"]
    inflight = w.ReconcileDecision(verified.source_generation, verified.change_generation,
                                   None, coverage_reasons=("v2_inflight",))
    before = w.apply_reconciliation(verified, inflight, rebase_at=late.received_at)
    assert before.state == expected["inflight_state"]
    assert before.state == "incomplete_coverage"
    assert verified.source_generation == expected["source_before"]
    assert before.confirmed.stats[0].active_ms == expected["unchanged_ms"]
    after = w.record_v2_commit(verified)
    assert after.state == expected["after_commit_state"]
    assert after.source_generation == expected["source_after"]
    assert w.apply_reconciliation(after, inflight, rebase_at=late.received_at) is None
    missing = w.ReconcileDecision(after.source_generation, after.change_generation,
                                  None, coverage_reasons=("v2_coverage_unknown",))
    unknown = w.apply_reconciliation(after, missing, rebase_at=late.received_at)
    assert unknown.state == "incomplete_coverage"
    assert unknown.coverage_reasons == ("v2_coverage_unknown",)
    assert unknown.confirmed.stats[0].active_ms == expected["unchanged_ms"]


@pytest.mark.parametrize("index", (0, 1, 2))
def test_602_e_preserve_v2_tool_context_and_exclude_presence(index):
    item = CASES["602-Д"]["v2_distinctions"][index]
    record = AcceptedEvent.model_validate_json(json.dumps(item["source"]))
    expected = item["expected"]
    assert w.v2_context(record) == (tuple(expected) if expected is not None else None)


def test_602_f_repair_fences_larger_old_epoch_and_zero_result():
    case = CASES["602-Е"]
    value = snapshot(case["offline_body"]["windows"][0])
    initial = w.receive_snapshot(w.WindowState(), value).state
    decision = w.ReconcileDecision(initial.source_generation, initial.change_generation,
                                   stats(case["repair_stats"]), (("backend", "backend-v1"),))
    repaired = w.apply_reconciliation(initial, decision, rebase_at=CLOCK)
    expected = case["expected"]
    assert repaired.confirmed.stats[0].active_ms == expected["repaired_ms"]
    assert repaired.confirmed.server_epoch == expected["epoch"]
    assert repaired.confirmed.revision == expected["revision"]
    replay = w.receive_snapshot(repaired, snapshot(case["stale_snapshot"]))
    assert replay.code == expected["replay_code"]
    assert replay.state is repaired
    assert w.apply_reconciliation(repaired, decision, rebase_at=CLOCK) is None
    zero = w.ReconcileDecision(repaired.source_generation, repaired.change_generation, ())
    fenced = w.apply_reconciliation(repaired, zero, rebase_at=CLOCK)
    assert fenced.confirmed.server_epoch == 3
    assert fenced.confirmed.stats == ()
    assert w.receive_snapshot(fenced, value).code == "stale_epoch"


def test_602_f_offline_reload_ack_and_lifecycle():
    case = CASES["602-Е"]
    persisted = json.dumps(case["offline_body"], separators=(",", ":"))
    (value,) = w.WindowBatch.model_validate_json(persisted).windows
    first = w.receive_snapshot(w.WindowState(), value)
    assert first.state.confirmed.stats[0].active_ms == case["expected"]["offline_ms"]
    # The same serialized outbox body survives reload with no token.
    (reloaded,) = w.WindowBatch.model_validate_json(persisted).windows
    assert reloaded.model_dump_json() == value.model_dump_json()
    assert first.state == w.receive_snapshot(first.state, reloaded).state
    owner = UUID(FIXTURE["student_id"])
    current = UUID(case["new_login_same_lifecycle"])
    other = UUID(case["new_lifecycle"])
    acks = {
        name: w.WindowItemAck.model_validate_json(json.dumps(case[name]))
        for name in ("foreign_ack", "pending_ack", "correct_ack")
    }
    accepted = [
        name for name, ack in acks.items()
        if w.can_remove_confirmed_snapshot(reloaded, ack, owner=owner, authenticated_subject=current)
    ]
    assert accepted == case["expected"]["remove_after"]
    assert w.build_ack(0, reloaded, w.receive_snapshot(first.state, reloaded)) == acks["correct_ack"]
    assert not w.can_remove_confirmed_snapshot(reloaded, acks["correct_ack"], owner=owner, authenticated_subject=other)
    assert current == owner and (other == owner) == case["expected"]["survives_new_lifecycle"]
    wrong_index = acks["correct_ack"].model_copy(update={"index": 1})
    assert not w.can_remove_confirmed_snapshot(reloaded, wrong_index, owner=owner, authenticated_subject=current)
    with pytest.raises(ValidationError):
        w.WindowBatchAck(protocol_version=1, items=(wrong_index,))


@pytest.mark.parametrize(("amount", "catalog", "final", "outcome"), (
    (470000, "backend-v1", False, ("conflict", "non_monotonic", 1)),
    (490000, "backend-v2", False, ("conflict", "catalog_conflict", 1)),
    (480000, "backend-v1", True, ("committed", "committed", 1)),
    (490000, "backend-v1", False, ("committed", "committed", 2)),
))
def test_monotonic_updates_versions_and_revision(amount, catalog, final, outcome):
    original = CASES["602-В"]["steps"][0]["snapshot"]
    first = w.receive_snapshot(w.WindowState(), snapshot(original)).state
    raw = {**original, "seq": 5, "catalog_versions": {"backend": catalog}, "client_final": final,
           "stats": [{**original["stats"][0], "active_ms": amount}]}
    result = w.receive_snapshot(first, snapshot(raw))
    assert (result.status, result.code, result.state.confirmed.revision) == outcome
    if final:
        assert result.state.state == "pending_reconciliation"
        assert w.receive_snapshot(result.state, snapshot({**raw, "seq": 6})).code == "finalized"


def test_time_policy_bounds_and_version():
    value = snapshot(CASES["602-В"]["steps"][0]["snapshot"])
    start = datetime.fromisoformat("2026-11-27T12:00:00Z")
    end = datetime.fromisoformat("2026-11-27T12:15:00Z")
    assert w.window_bounds(value) == (start, end)
    assert w.validate_window_age(value, start - timedelta(seconds=120)) is None
    assert w.validate_window_age(value, start - timedelta(seconds=121)) == "future_clock"
    assert w.validate_window_age(value, end + timedelta(hours=24)) is None
    assert w.validate_window_age(value, end + timedelta(hours=24, seconds=1)) == "too_late"
    with pytest.raises(ValidationError):
        w.WindowBatch.model_validate_json(json.dumps({"protocol_version": True, "windows": [value.model_dump(mode="json")]}))


@pytest.mark.parametrize("mutation", (
    {"grant_version": True}, {"aud": "analytics"}, {"exp": 1795782000}, {"tool_scopes": [{"tool_id": "catalog", "course_id": "sql"}]},
))
def test_grant_claims_reject_invalid_audience_time_and_tool_scope(mutation):
    claims = FIXTURE["grants"]["A"]
    with pytest.raises(ValidationError):
        w.WindowGrantClaims.model_validate_json(json.dumps({**claims, **mutation}))


def test_confirmed_handoff_preserves_base_and_fences_previous_writer():
    source = snapshot(CASES["602-В"]["steps"][0]["snapshot"])
    first = w.receive_snapshot(w.WindowState(), source).state
    decision = w.ReconcileDecision(first.source_generation, first.change_generation, source.stats)
    verified = w.apply_reconciliation(first, decision, rebase_at=CLOCK)
    writer = w.Writer(writer_id=GRANTS["B"].writer_id, writer_generation=GRANTS["B"].writer_generation)
    transferred = w.handoff_writer(verified, writer, rebase_at=CLOCK)
    assert transferred.confirmed.stats == first.confirmed.stats
    assert transferred.confirmed.revision == 2
    assert transferred.confirmed.rebase_at == CLOCK
    assert w.receive_snapshot(transferred, source).code == "stale_epoch"
    new = {**source.model_dump(mode="json"), **writer.model_dump(mode="json"), "server_epoch": 2, "seq": 1}
    resumed = w.receive_snapshot(transferred, snapshot(new))
    assert resumed.status == "committed"
    assert resumed.state.confirmed.revision == 2

