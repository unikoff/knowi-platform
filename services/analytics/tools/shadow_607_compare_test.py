"""Fixture checks for the 607 acceptance report, without access to a live target."""

from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID

import pytest

from tools.shadow_607_compare import (
    DEFAULT_EXPECTED, BrowserWitness, Case, add_cases, browser_witnesses, key_from, managed_manifest,
    SCHEMA_ROOT, WINDOW_MIGRATION, browser_acks, final_run_cases, frozen_candidate_schema, model_cases, raw_v2_windows, read_json, stats_from, summarize, version_digest,
)
from tools.shadow_607_compare_merge import combine

RUN = "00000000-0000-4000-8000-000000000607"
PLAN = read_json(DEFAULT_EXPECTED)
MEMBERS = {name: UUID(subject) for name, subject in PLAN["subjects"].items()}
T0 = datetime.fromisoformat("2026-11-27T10:00:00+03:00")
VERSION = sha256(b"synthetic verifier test build").hexdigest()
FIRST = {
    "key": {"student_id": str(MEMBERS["first"]), "activity_date": "2026-11-27", "slot_no": 41},
    "outcome": "matched", "repairable": True,
    "before": {"stats": [
        {"tool_id": "theory", "course_id": "backend", "lesson_id": "lesson-64c9d597a164bbdb4f145d5a", "active_ms": 60000},
    ], "revision": 1, "context_diff": []},
    "diagnostics": {"coverage_reasons": [], "state": "verified", "source_generation": 1, "reconciled_generation": 1},
}
LOST = {
    "key": {"student_id": str(MEMBERS["lost"]), "activity_date": "2026-11-27", "slot_no": 45},
    "outcome": "both_lost", "repairable": False,
    "before": {"stats": [], "revision": None, "context_diff": [{"expected_ms": 40000, "observed_ms": 0}]},
    "diagnostics": {"coverage_reasons": ["absent_both", "v2_coverage_unknown"], "state": "pending_reconciliation"},
}


def _scan(*rows, truncated=False):
    return {"target": "synthetic-fixture", "mode": "read_only", "rows": rows,
            "denominator": len(rows), "truncated": truncated}


def _witness(row=FIRST):
    return {key_from(row["key"]): BrowserWitness(stats_from(row["before"]["stats"]), 1, "fixture-browser.json")}


def test_model_contains_all_six_normal_windows_and_loss_of_both_paths():
    cases = model_cases(PLAN, MEMBERS)
    assert len(cases) == 7
    assert sum(case.group == "normal" for case in cases.values()) == 6
    assert cases[key_from(LOST["key"])].expected[0].active_ms == 40000
    shifted = model_cases(PLAN, MEMBERS, date_shift=1)
    assert max(case.key.activity_date.isoformat() for case in shifted.values()) == "2026-11-29"
    entries = managed_manifest(cases, cohort_t0=T0)["windows"]
    assert sum(entry.get("v2_source_complete", False) for entry in entries) == 6
    first = next(entry for entry in entries if entry["key"] == FIRST["key"])
    assert first["expected_stats"] == FIRST["before"]["stats"]


def test_expected_both_lost_is_included_but_missing_from_export_is_open():
    cases = model_cases(PLAN, {name: MEMBERS[name] for name in ("first", "lost")})
    result = summarize(cases, _scan(FIRST, LOST), _witness(), run=RUN, versions_sha256=VERSION)
    assert result["verdict"] == "pass"
    assert result["counts"]["denominator"] == 2 and result["counts"]["both_lost"] == 1
    assert result["groups"]["normal"]["mismatch_before"] == 0
    assert result["groups"]["fault"]["mismatch_before"] == result["groups"]["fault"]["mismatch_after"] == 1
    missing = summarize(cases, _scan(FIRST), _witness(), run=RUN, versions_sha256=VERSION)
    assert missing["verdict"] == "open"
    assert missing["counts"]["denominator"] == 2 and missing["counts"]["unobserved"] == 1
    assert missing["open_keys"] == [LOST["key"]]


def test_match_after_repair_does_not_hide_nominal_initial_difference():
    cases = model_cases(PLAN, {"first": MEMBERS["first"]})
    fixed = {**FIRST, "expected_hash": "abc", "durable_repair": {
        "expected_hash": "abc", "before": {"revision": 1, "context_diff": [{"expected_ms": 60000, "observed_ms": 120000}]},
    }}
    result = summarize(cases, _scan(fixed), _witness(), run=RUN, versions_sha256=VERSION)
    assert result["verdict"] == "open"
    assert result["counts"]["mismatch_before"] == 1 and result["counts"]["mismatch_after"] == 0
    assert result["windows"][0]["before_source"] == "durable_repair"


def test_old_only_and_v3_only_conflict_stay_in_denominator():
    cases = model_cases(PLAN, {"first": MEMBERS["first"]})
    stats = FIRST["before"]["stats"]
    old_key = {**FIRST["key"], "student_id": str(MEMBERS["multi"])}
    conflict_key = {**old_key, "slot_no": 42}
    add_cases(cases, {"schema_version": 1, "run_id": RUN, "windows": [
        {"key": old_key, "group": "fault", "expected_outcome": "old_only", "v2_source_complete": False,
         "expected_stats": stats, "scenario": "old"},
        {"key": conflict_key, "group": "fault", "expected_outcome": "unknown", "v2_source_complete": False,
         "expected_stats": stats, "scenario": "writers", "require_reason": "writer_conflict"},
    ]}, run=RUN, members=MEMBERS.values())
    old = {"key": old_key, "outcome": "old_only", "expected_stats": stats,
           "diagnostics": {"coverage_reasons": ["old_only"]}, "before": {"stats": []}}
    conflict = {"key": conflict_key, "outcome": "unknown", "before": {"stats": stats, "revision": 1},
                "diagnostics": {"coverage_reasons": ["writer_conflict", "v3_only", "v2_coverage_unknown"]}}
    report = summarize(cases, _scan(FIRST, old, conflict), _witness(), run=RUN, versions_sha256=VERSION)
    assert report["verdict"] == "pass"
    assert report["counts"]["denominator"] == 3
    for coin in ("old_only", "unknown", "v3_only", "conflict"):
        assert report["counts"][coin] == 1
    assert report["reason_counts"]["writer_conflict"] == 1
    assert report["groups"]["fault"]["unknown_after"] == 2
    assert all(not row.get("v2_source_complete") for row in managed_manifest(cases, cohort_t0=T0)["windows"]
               if row["key"] in (old_key, conflict_key))


def test_empty_truncated_wrong_version_and_unplanned_do_not_pass():
    cases = model_cases(PLAN, {"first": MEMBERS["first"]})
    assert summarize({}, _scan(), {}, run=RUN, versions_sha256=VERSION)["verdict"] == "open"
    assert summarize(cases, _scan(FIRST, truncated=True), _witness(), run=RUN, versions_sha256=VERSION)["verdict"] == "open"
    assert summarize(cases, _scan(FIRST), _witness(), run=RUN, versions_sha256=None)["verdict"] == "open"
    assert summarize(cases, _scan(FIRST, LOST), _witness(), run=RUN, versions_sha256=VERSION)["verdict"] == "open"


def test_real_browser_shapes_require_ack_and_same_full_key():
    ack = {"status": "committed", "activity_date": FIRST["key"]["activity_date"], "slot_no": 41,
           "confirmed": {"revision": 1, "stats": FIRST["before"]["stats"]}}
    normalized = {"run_id": RUN, "schema_version": 1,
                  "windows": [{"key": FIRST["key"], "before": FIRST["before"], "ack": ack}]}
    legacy = {"run": RUN, "subject_ref": FIRST["key"]["student_id"], "commit": {"body": {"items": [ack]}},
              "postgres_before_replay": [{**FIRST["key"], **FIRST["before"]}]}
    assert browser_witnesses(normalized, run=RUN, source="fixture-browser.json") == _witness()
    assert browser_witnesses(legacy, run=RUN, source="fixture-browser.json") == _witness()
    with pytest.raises(ValueError, match="different run"):
        browser_witnesses(legacy, run=str(MEMBERS["first"]), source="fixture-browser.json")
    ack["slot_no"] = 40
    with pytest.raises(ValueError, match="differs"):
        browser_witnesses(normalized, run=RUN, source="fixture-browser.json")


def test_component_bundle_and_raw_browser_event_bind_normal_comparison(tmp_path):
    case = model_cases(PLAN, {"first": MEMBERS["first"]})
    key = key_from(FIRST["key"])
    raw_event = {"event_id": RUN, "started_at": "2026-11-27T07:00:00Z", "ended_at": "2026-11-27T07:01:00Z"}
    ack = {"status": "committed", "activity_date": key.activity_date.isoformat(), "slot_no": key.slot_no,
           "payload_hash": VERSION, "confirmed": {"revision": 1, "stats": FIRST["before"]["stats"]}}
    document = {
        "run_id": RUN, "schema_version": 1, "student_id": str(key.student_id),
        "windows": [{"key": FIRST["key"], "ack": ack}],
        "requests": [{"path": "/api/analytics/events/batch", "status": 200, "body": {"events": [raw_event]},
                      "response": {"items": [{"event_id": RUN, "status": "accepted"}]}}],
    }
    version = tmp_path / "build.json"
    labels = ("web", "core", "analytics_runtime_including_collector_and_worker", "schema")
    version.write_text(json.dumps({"components": {label: {"sha256": VERSION} for label in labels}}), encoding="utf-8")
    assert version_digest(version, RUN, [document]) is None
    document["versions_sha256"] = sha256(version.read_bytes()).hexdigest()
    assert version_digest(version, RUN, [document]) == document["versions_sha256"]

    actual = {**FIRST, "diagnostics": {**FIRST["diagnostics"],
        "v3_confirmed": {"server_epoch": 1, "payload_hash": VERSION},
        "v2_confirmed": [{"event_id": RUN}],
    }}
    v2_windows = raw_v2_windows(document)
    v3_acks = browser_acks(document, source="fixture.json")
    report = summarize(case, _scan(actual), {}, run=RUN, versions_sha256=VERSION,
                       v2_windows=v2_windows, v3_acks=v3_acks)
    assert report["verdict"] == "pass"
    assert report["windows"][0]["v2_browser_and_database_events"][0]["active_ms_in_slot"] == 60000
    raw_event["started_at"], raw_event["ended_at"] = "2026-11-27T08:00:00Z", "2026-11-27T08:01:00Z"
    wrong_slot = summarize(case, _scan(actual), {}, run=RUN, versions_sha256=VERSION,
                           v2_windows=raw_v2_windows(document), v3_acks=v3_acks)
    assert wrong_slot["verdict"] == "open"
    assert wrong_slot["windows"][0]["v2_browser_and_database_events"] == []


def test_merged_run_keys_remain_distinct_and_build_changes_are_open():
    case = model_cases(PLAN, {"first": MEMBERS["first"]})
    first = summarize(case, _scan(FIRST), _witness(), run=RUN, versions_sha256=VERSION)
    second = {**first, "run_id": str(MEMBERS["first"]), "target": "other-fixture"}
    report = combine([first, second, first])
    assert report["verdict"] == "pass"
    assert report["counts"]["denominator"] == report["counts"]["verified"] == 2
    assert {row["run_id"] for row in report["windows"]} == {RUN, second["run_id"]}
    other_build = {**second, "versions_sha256": "a" * 64}
    assert combine([first, other_build])["verdict"] == "open"


def test_local_target_requires_validated_settings_and_stays_read_only(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from pydantic import SecretStr, ValidationError
    from tools import check_student_windows as diagnostic

    target = "analytics_live_" + UUID(RUN).hex
    url = f"postgresql+asyncpg://{target}_processor:fixture-only@127.0.0.1:57392/{target}"
    prefix = ("test-authority", 0)
    args = SimpleNamespace(target=target, student_id=(MEMBERS["first"],), limit=10,
                           cohort_t0=T0, through=T0.replace(hour=11), manifest=None, repair=False)
    # The original CLI/settings boundary must still reject local-live configuration.
    with pytest.raises(ValidationError, match="only synthetic mode"):
        asyncio.run(diagnostic.inspect_windows(args, url, privacy_prefix=prefix))

    validated = SimpleNamespace(database_url=SecretStr(url))
    with pytest.raises(diagnostic.TargetError, match="matching read-only"):
        asyncio.run(diagnostic.inspect_windows(args, url.replace(":57392", ":61234"),
                                               privacy_prefix=prefix, readonly_settings=validated))
    args.repair = True
    with pytest.raises(diagnostic.TargetError, match="matching read-only"):
        asyncio.run(diagnostic.inspect_windows(args, url, privacy_prefix=prefix, readonly_settings=validated))
    args.repair = False

    operations = []

    class DummySession:
        def __init__(self):
            self.info = {}

        async def execute(self, statement):
            operations.append(str(statement).split())
            return SimpleNamespace(one_or_none=lambda: prefix)

    class DummyDatabase:
        def __init__(self, settings):
            assert settings is validated

        async def close(self):
            pass

    class DummyStore:
        def __init__(self, database, **_):
            self.database = database

        async def diagnostic_keys(self, **_):
            session = DummySession()
            await self.database.transaction_guard(session)
            await self.database.transaction_guard(session)
            return ()

    monkeypatch.setattr(diagnostic, "Database", DummyDatabase)
    monkeypatch.setattr(diagnostic, "PostgresWindowDiagnosticStore", DummyStore)
    report = asyncio.run(diagnostic.inspect_windows(args, url, privacy_prefix=prefix, readonly_settings=validated))
    assert report["mode"] == "read_only" and report["denominator"] == 0
    assert operations[0] == ["SET", "TRANSACTION", "READ", "ONLY"]
    assert len(operations) == 3 and all(query[:3] == ["SELECT", "authority_id,", "applied_through"] for query in operations[1:])


def test_planned_but_unrun_window_is_open_without_fabricating_mismatch_after():
    case = model_cases(PLAN, {"first": MEMBERS["first"]})
    known_absence = {**LOST, "key": FIRST["key"]}
    snapshot = summarize(case, _scan(known_absence), {}, run=RUN, versions_sha256=VERSION, loss_witnesses=set())
    assert snapshot["verdict"] == "open"
    assert snapshot["counts"]["both_lost"] == 0
    assert snapshot["counts"]["unknown_before"] == snapshot["counts"]["unknown_after"] == 1
    assert snapshot["counts"]["mismatch_before"] == snapshot["counts"]["mismatch_after"] == 0


def test_final_run_manifest_binds_t0_and_needs_no_date_inference():
    plan = {
        "run_id": RUN, "timezone": "Europe/Moscow", "cohort_t0": T0.isoformat(),
        "declared_before_browser_activity": True, "common_lesson": {"course_id": "backend", "lesson_id": "71"},
        "cases": [
            ["midnight", str(MEMBERS["multi"]), "2026-11-27", 96, {"theory": 10000}, "comparable"],
            ["reload", str(MEMBERS["new_lifecycle"]), "2026-11-27", 41, {"theory": 480000},
             "recovery expected; v2 completeness determined independently"],
        ],
    }
    cases = final_run_cases(plan, run=RUN, members=MEMBERS.values(), cohort_t0=T0)
    assert [item.outcome for item in cases.values()] == ["matched", "unknown"]
    assert [item.expected[0].active_ms for item in cases.values()] == [10000, 480000]
    with pytest.raises(ValueError, match="run and T0"):
        final_run_cases(plan, run=RUN, members=MEMBERS.values(), cohort_t0=T0.replace(hour=11))



@pytest.mark.parametrize("fault", [None, "wrong-tree", "wrong-a015", "wrong-scope"])
def test_candidate_schema_binds_tree_and_single_migration(tmp_path, monkeypatch, fault):
    migration_paths = [SCHEMA_ROOT + "env.py", WINDOW_MIGRATION]
    freeze = []
    tree = sha256()
    for name in migration_paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
        digest = sha256(path.read_bytes())
        freeze.append({"path": name, "sha256": digest.hexdigest()})
        tree.update(name.encode("utf-8") + bytes(1) + digest.digest())
    tree_digest = tree.hexdigest()
    single_digest = freeze[1]["sha256"]
    assert tree_digest != single_digest
    if fault == "wrong-scope":
        migration_paths.append(SCHEMA_ROOT + "versions/not_frozen.py")
        (tmp_path / migration_paths[-1]).touch()
    monkeypatch.setattr("tools.shadow_607_compare.subprocess.check_output",
                        lambda *_args, **_kwargs: chr(0).join(migration_paths).encode("utf-8"))

    def json_file(name, doc):
        target = tmp_path / name
        target.write_text(json.dumps(doc), encoding="utf-8")
        return target

    builds = json_file("builds.json", {"schema": tree_digest})
    source = json_file("source.json", {"components": {"schema": {"files": 2, "sha256": tree_digest}}})
    frozen = json_file("freeze.json", freeze)
    doc = {
        "candidate_builds_sha256": sha256(builds.read_bytes()).hexdigest(),
        "candidate_source_sha256": sha256(source.read_bytes()).hexdigest(),
        "schema_sha256": tree_digest if fault == "wrong-a015" else single_digest,
        "schema_tree_sha256": single_digest if fault == "wrong-tree" else tree_digest,
    }
    failure = {"wrong-tree": "schema tree digest", "wrong-a015": "a015 file digest",
               "wrong-scope": "different file scope"}
    if fault is not None:
        with pytest.raises(ValueError, match=failure[fault]):
            frozen_candidate_schema(doc, builds, source, frozen, tmp_path)
    else:
        evidence = frozen_candidate_schema(doc, builds, source, frozen, tmp_path)
        assert evidence["schema_tree_sha256"] == tree_digest
        assert evidence["a015_file_sha256"] == single_digest
