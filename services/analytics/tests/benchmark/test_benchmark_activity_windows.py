import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import benchmark_activity as base
import benchmark_activity_windows as windows
import benchmark_windows_fixture as staging
import benchmark_windows_postgres as dedicated_pg
from benchmark_windows_fixture import fixture_inputs
from benchmark_windows_target import (
    CONNECTION_COUNT_SCOPE, DOCKER_METRICS, MIB, WORKER_COUNTERS, Observer, bounded_lock_waits, index_footprint,
    metrics_overflow,
    validate_dsn, worker_metrics,
)
from pymentor_analytics.contracts.reports_v1 import ActivityReport
from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.contracts.windows_v1 import WindowState, build_ack, receive_snapshot

PAIR = UUID("ac091853-1ebe-4aa0-8eef-cd20b6c70f9d")
ANCHOR = datetime.fromisoformat("2026-11-27T20:45:00+00:00")
COURSES = [windows.Course("backend", "one", "71"), windows.Course("backend_copy", "two", "41")]


def plan(stage=10, mode="full"):
    return windows.Plan(base.profile_load(base.ROOT / "bench/profile-v1.json"), stage, PAIR, ANCHOR, mode, COURSES)


@pytest.mark.parametrize(("stage", "mode", "active", "overlap"), [
    (10, "full", 6, 2), (15, "full", 9, 2), (25, "full", 15, 3), (50, "full", 30, 6), (50, "smoke", 30, 6),
])
def test_deterministic_wire_and_independent_window_oracle(stage, mode, active, overlap):
    workload = plan(stage, mode)
    minutes = [tuple(workload.v2(i)) for i in range(workload.minutes)]
    windows_seen = {}
    for _, subject, snapshot in workload.snapshots(minutes):
        coordinates = subject, snapshot.activity_date, snapshot.slot_no
        windows_seen[coordinates] = sorted(
            (row.tool_id, row.course_id, row.lesson_id, row.active_ms) for row in snapshot.stats
        )
    assert (workload.active, workload.overlap) == (active, overlap)
    assert all(len(minute) == active + overlap for minute in minutes)
    assert windows_seen == workload.expected_windows()
    assert {coordinate[2] for coordinate in windows_seen} == ({96, 1} if mode == "full" else {96})
    assert windows.work_hash(minutes[0]) == windows.work_hash(tuple(plan(stage, mode).v2(0)))
    assert windows.work_hash(minutes[0]) != windows.work_hash(minutes[1])


def test_overlap_is_capped_in_old_report():
    workload = plan()
    facts = [AcceptedEvent.model_validate_json(json.dumps(dict(
        producer_namespace="browser:" + str(subject), subject_ref=str(subject), source="browser",
        event=event.model_dump(mode="json"), received_at=event.ended_at.isoformat())))
        for i in range(workload.minutes) for subject, event in workload.v2(i)]
    assert windows.report_expected(facts, workload.measure_start, workload.end, workload.end) == {
        "active_ms": 5_400_000, "practice_ms": 2_880_000, "theory_ms": 2_520_000, "unique_visitors": 6,
    }


def test_pg_oracle_refuses_no_rows_wrong_revision_and_baseline_writes():
    expected = plan().expected_windows()
    receipts = dict.fromkeys(expected, 3)
    with pytest.raises(base.ObservedViolation, match="denominator"):
        windows.match_rows({}, {}, {}, True)
    with pytest.raises(base.ObservedViolation, match="key set"):
        windows.match_rows(expected, {}, receipts, True)
    with pytest.raises(base.ObservedViolation, match="revision"):
        windows.match_rows(expected, {key: (stats, 2) for key, stats in expected.items()}, receipts, True)
    with pytest.raises(base.ObservedViolation, match="key set"):
        windows.match_rows(expected, {key: (stats, 2) for key, stats in expected.items()}, {}, False)
    windows.match_rows(expected, {key: (stats, 3) for key, stats in expected.items()}, receipts, True)
    windows.match_rows(expected, {}, {}, False)


@pytest.mark.parametrize("matching", [True, False])
def test_core_shadow_write_requires_exact_ack(matching):
    workload = plan()
    _, subject, snapshot = next(workload.snapshots([tuple(workload.v2(0))]))
    phase = windows.Phase(SimpleNamespace(url=lambda role: "http://127.0.0.1:18741"), workload, {})
    phase.sessions[subject] = "session=private-value"
    phase.pending = 1
    now = datetime.fromisoformat("2026-11-27T21:00:00+00:00")
    status = receive_snapshot(WindowState(), snapshot)
    item = build_ack(0, snapshot, status)
    if not matching:
        item = item.model_copy(update={"slot_no": 42})

    def reply(request):
        assert request.headers["Cookie"] == "session=private-value"
        if request.url.path.endswith("/grant"):
            data = dict(protocol_version=1, writer_id=str(snapshot.writer_id), writer_generation=1,
                        grant="private-token", subject_ref=str(subject), server_time=now.isoformat(),
                        expires_at=(now + timedelta(seconds=30)).isoformat())
            return httpx.Response(200, json=data)
        assert request.headers["Authorization"] == "Bearer private-token"
        assert json.loads(request.content)["windows"] == [snapshot.model_dump(mode="json")]
        return httpx.Response(200, json={"protocol_version": 1, "items": [item.model_dump(mode="json")]})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            phase.client = client
            if matching:
                key, revision = await phase.send_v3(subject, snapshot, "measure")
                assert revision == 1
                assert phase.receipts == {key: 1} and phase.pending == 0
            else:
                with pytest.raises(base.ObservedViolation, match="exact committed"):
                    await phase.send_v3(subject, snapshot, "measure")
                assert not phase.receipts and phase.pending == 1

    asyncio.run(execute())


def test_unknown_mutation_outcome_requires_reconciliation_and_never_retries():
    sent = []

    def timeout(request):
        sent.append(request)
        raise httpx.ReadTimeout("private body should remain hidden", request=request)

    workload = plan()
    target = SimpleNamespace(url=lambda role: "http://127.0.0.1:18742", key=Ed25519PrivateKey.generate(),
                             kid="synthetic", issuer="bench-synthetic")
    phase = windows.Phase(target, workload, {})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            phase.client = client
            with pytest.raises(base.ObservedViolation, match="reconcile before retry"):
                await phase.send_v2(*next(workload.v2(0)), "measure")

    asyncio.run(execute())
    assert len(sent) == 1 and phase.requests == {"measure:v2:timeout": 1}
    assert phase.evidence["unknown_write"] == {"event_id": str(next(workload.v2(0))[1].event_id)}


def report(amount):
    start = ANCHOR
    values = dict(unique_visitors=6, active_ms=amount, theory_ms=amount, practice_ms=0)
    return ActivityReport.model_validate(dict(
        status="ok", quality="unknown", as_of=start + timedelta(minutes=1), lag_seconds=None, timezone="Europe/Moscow",
        course_id=None, from_time=start, to_time=start + timedelta(minutes=1), totals=values,
        daily=[dict(date=start.date(), **values)],
    ))


def test_live_lag_requires_visible_activity_not_http_success():
    workload = plan()
    phase = windows.Phase(None, workload, {})
    phase.admin_backlog = [(100, 60_000)]
    phase.observe_admin(report(59_999), 60, observed_at=101)
    assert not phase.lag
    phase.observe_admin(report(60_000), 60, observed_at=115)
    assert phase.lag == [15]
    phase.admin_backlog = [(100, 60_000)]
    with pytest.raises(base.ObservedViolation, match="within 60 seconds"):
        phase.observe_admin(report(60_000), 60, observed_at=161)


def test_local_dsn_rejects_hidden_remote_host_or_database_override():
    dsn = "host=127.0.0.1 port=57392 dbname=analytics user=bench password=private"
    validate_dsn(dsn, "analytics", "bench", 57392)
    for modified in (dsn + " service=elsewhere", dsn + " hostaddr=192.0.2.1", dsn + " dbname=other"):
        with pytest.raises(ValueError):
            validate_dsn(modified, "analytics", "bench", 57392)


def test_lock_wait_quantile_is_upper_bound_and_incomplete_waits_stay_unknown():
    start = datetime.fromisoformat("2026-11-27T20:44:00+00:00")
    log = """
2026-11-27 20:45:00.002 UTC [42] db=bench LOG:  process 42 still waiting for ShareLock on transaction after 10.012 ms
2026-11-27 20:45:00.004 UTC [42] db=bench LOG:  process 42 acquired ShareLock on transaction after 13.445 ms
2026-11-27 20:45:01.004 UTC [43] db=other LOG:  process 43 acquired ShareLock on transaction after 9999.445 ms
"""
    end = ANCHOR + timedelta(seconds=2)
    result = bounded_lock_waits(log, database="bench", start=start, end=end, censor_ms=10, waiting_at_end=0)
    assert result["lock_p95_upper_ms"] == 13.445 and result["lock_waits_reported"] == 1
    result = bounded_lock_waits(log, database="bench", start=start, end=ANCHOR + timedelta(milliseconds=3),
                                censor_ms=10, waiting_at_end=1)
    assert result["lock_p95_upper_ms"] is None and result["lock_waits_unresolved"] == 1
    assert bounded_lock_waits("", database="bench", start=start, end=end, censor_ms=10,
                              waiting_at_end=0)["lock_p95_upper_ms"] == 10
    below_log_threshold = bounded_lock_waits("", database="bench", start=start, end=end, censor_ms=10, waiting_at_end=1)
    assert below_log_threshold["lock_p95_upper_ms"] is None and below_log_threshold["lock_waits_reported"] == 0
    different_lock = log.replace("acquired ShareLock on transaction", "acquired AccessShareLock on relation")
    unresolved = bounded_lock_waits(different_lock, database="bench", start=start, end=end, censor_ms=10,
                                    waiting_at_end=0)
    assert unresolved["lock_p95_upper_ms"] is None and unresolved["lock_waits_unresolved"] == 1


def test_missing_pool_overflow_cannot_be_read_as_zero():
    assert metrics_overflow("analytics_database_pool_overflow_known 0\n") is None
    assert metrics_overflow("analytics_database_pool_overflow_known 1\n") is None
    assert metrics_overflow("analytics_database_pool_overflow_known 1\nanalytics_database_pool_overflow 0\n") == 0


def evidence():
    workload = plan()
    records = []
    for mode in ("v2_only", "shadow"):
        planned = windows.plan_evidence(workload, [tuple(workload.v2(i)) for i in range(workload.minutes)],
                                        tuple(workload.v2(-1)), "full")
        planned["status"] = "OBSERVED"
        planned["phases"] = [dict(mode=mode, status="OBSERVED", database_sha256=mode, core_database_sha256=mode,
                                  measurement_seconds_observed=900, collector={"p95_ms": 230, "count": 120},
                                  query={"p95_ms": 380, "count": 180},
                                  v3_core_to_commit_ack={"count": 90 if mode == "shadow" else 0},
                                  live_lag_seconds=1, drain_seconds=1, v3_client_backlog=0,
                                  v3_combined_backlog_at_enqueue=0,
                                  pg_window_oracle={"status": "PASS", "denominator": 12,
                                                    "observed": 12 if mode == "shadow" else 0},
                                  report_oracle={"status": "PASS", "totals": {"active_ms": 5_400_000}},
                                  resources=dict(cpu_cores=2, rss_mib=300, pool_overflow=0, connections=4,
                                                 connection_count_scope=CONNECTION_COUNT_SCOPE,
                                                 deadlocks=0, lock_p95_upper_ms=10, lock_waiters_at_end=0,
                                                 index_footprint={"analytics": {"at_start_bytes": 0, "at_end_bytes": 0}},
                                                 v3_worker_backlog=0,
                                                 wal_mib_per_120s=1, database_mib_per_120s=1, window_updates=0, samples=14,
                                                 core_revision="core-rev", pg_revision="a015", postgres="16.16",
                                                 versions={}, pg_target_sha256="same", analytics_pool_by_role={}))]
        records.append(planned)
    return records


def test_pair_combiner_rejects_calendar_and_preserves_missing_metric():
    baseline, shadow = evidence()
    good = windows.combine(baseline, shadow)
    assert good["status"] == "PASS" and good["cohort_expansion"] == "UNRUN"
    shadow["phases"][0]["resources"]["lock_p95_upper_ms"] = None
    unknown = windows.combine(baseline, shadow)
    assert unknown["status"] == "UNRUN" and unknown["gates"]["lock_p95_upper_ms"]["status"] == "UNRUN"
    shadow["phases"][0]["resources"]["lock_p95_upper_ms"] = 150
    assert windows.combine(baseline, shadow)["status"] == "FAIL"
    shadow["phases"][0]["pg_window_oracle"]["observed"] = 0
    with pytest.raises(ValueError, match="denominator"):
        windows.combine(baseline, shadow)
    shadow["v2_input_sha256"] = "changed"
    with pytest.raises(ValueError, match="different workload"):
        windows.combine(baseline, shadow)


def test_pair_combiner_requires_qualified_connection_locks_and_indexes():
    baseline, shadow = evidence()
    resources = shadow["phases"][0]["resources"]
    for field in ("connection_count_scope", "lock_waiters_at_end", "index_footprint"):
        resources.pop(field)
    result = windows.combine(baseline, shadow)
    assert result["status"] == "UNRUN"
    assert {result["gates"][key]["status"]
            for key in ("connections", "lock_p95_upper_ms", "observed_index_footprint")} == {"UNRUN"}


def test_index_footprint_preserves_both_boundaries_and_scope():
    business = ("analytics", "student_activity_windows")
    control = ("analytics", "student_activity_window_control")
    other = ("public", "bench_sentinel")
    first = {business: (1, 16), control: (2, 32), other: (3, 64)}
    last = {business: (1, 8), control: (2, 48), other: (3, 96)}
    assert index_footprint(first, last) == {
        "user_schemas": {"at_start_bytes": 112, "at_end_bytes": 152, "delta_bytes": 40},
        "analytics": {"at_start_bytes": 48, "at_end_bytes": 56, "delta_bytes": 8},
        business[1]: {"at_start_bytes": 16, "at_end_bytes": 8, "delta_bytes": -8},
        control[1]: {"at_start_bytes": 32, "at_end_bytes": 48, "delta_bytes": 16},
    }
    assert index_footprint(first, {**last, control: (4, 48)}) is None
    assert index_footprint(first, {business: (1, 8), control: (2, 48)}) is None


def test_docker_metric_shell_skips_only_disappeared_processes(tmp_path):
    shell = shutil.which("sh")
    if shell is None and sys.platform == "win32" and (git := shutil.which("git")):
        candidate = Path(git).parents[1] / "bin" / "sh.exe"
        if candidate.is_file():
            shell = str(candidate)
    if shell is None:
        pytest.skip("POSIX shell unavailable")
    (tmp_path / "cpu.stat").write_text("usage_usec 1234\n", encoding="ascii")
    proc = tmp_path / "proc"
    for pid, memory in ((1, 96), (2, 32)):
        folder = proc / str(pid)
        folder.mkdir(parents=True)
        (folder / "status").write_text(f"Name:\tfixture\nVmRSS:\t{memory} kB\n", encoding="ascii")
    (proc / "3").mkdir()
    script = DOCKER_METRICS.replace("/sys/fs/cgroup/cpu.stat", "cpu.stat").replace("/proc/[0-9]*/status", '"$@"')

    def sample(*status_files):
        return subprocess.run([shell, "-c", "PATH=/usr/bin:/bin:$PATH\n" + script, "fixture", *status_files],
                              cwd=tmp_path, capture_output=True, text=True, timeout=5)

    ordinary = sample("proc/1/status", "proc/2/status")
    assert (ordinary.returncode, ordinary.stdout.split(), ordinary.stderr) == (0, ["1234", "128"], "")
    vanished = sample("proc/1/status", "proc/4/status", "proc/2/status")
    assert (vanished.returncode, vanished.stdout.split(), vanished.stderr) == (0, ["1234", "128"], "")
    unreadable = sample("proc/1/status", "proc/3/status")
    assert unreadable.returncode == 21
    empty = sample("proc/4/status")
    assert empty.returncode == 22


def test_observer_classifies_docker_metric_error_without_output(monkeypatch):
    observer = Observer(SimpleNamespace(docker={"id": "owned"}, processes={}), None, None)

    def denied(*args):
        raise subprocess.CalledProcessError(21, args, output=b"private", stderr=b"private")

    monkeypatch.setattr(observer, "docker", denied)
    with pytest.raises(base.StopRun, match=r"^PostgreSQL CPU/RSS sample failed \(exit 21: process status unreadable\)$"):
        observer.machine()


def test_observer_normalizes_storage_rates_with_postgres_time(monkeypatch):
    connection = SimpleNamespace(execute=lambda _sql: SimpleNamespace(fetchall=list))
    target = SimpleNamespace(docker={"id": "owned"}, versions={}, analytics_pool_by_role={})
    observer = Observer(target, connection, SimpleNamespace(result=dict))
    observer.settings = {"server_version": "16"}
    observer.revision, observer.core_revision = "a015", "core"
    first = dict(monotonic=50, db_time=ANCHOR, reset_at=None, db_bytes=3 * MIB, wal_insert_bytes=5 * MIB,
                 cpu_seconds=10, rss_mib=256, deadlocks=0, connections=4, waiting_locks=0, v3_worker_backlog=0,
                 pool_overflow=0, window_updates=0, window_rows=0, window_worker=dict.fromkeys(WORKER_COUNTERS, 0),
                 index_sizes={})
    last = {**first, "monotonic": 110, "db_time": ANCHOR + timedelta(seconds=120), "db_bytes": 2 * MIB,
            "wal_insert_bytes": 7 * MIB, "cpu_seconds": 40, "connections": 6, "waiting_locks": 1}
    observer.bounds = {"start": first, "end": last}
    observer.samples = [first, last]
    monkeypatch.setattr(observer, "lock_metrics", lambda _start, _end, waiting_at_end:
                        {"lock_p95_upper_ms": None if waiting_at_end else 10})
    result = observer.result()
    assert result["database_measurement_seconds"] == 120
    assert result["database_mib_per_120s"] == -1
    assert result["wal_mib_per_120s"] == 2
    assert result["cpu_cores"] == .5
    assert result["connections"] == 6 and result["connection_count_scope"] == CONNECTION_COUNT_SCOPE
    assert result["waiting_locks_max_sampled"] == result["lock_waiters_at_end"] == 1
    assert result["lock_p95_upper_ms"] is None


def test_dry_run_refuses_live_targets(capsys):
    args = ["--stage", "50", "--mode", "smoke", "--pair", str(PAIR), "--anchor", ANCHOR.isoformat()]
    assert windows.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "UNRUN" and result["phases"] == []
    assert (result["planned_v2_measurement"], result["planned_query_measurement"],
            result["planned_v3_measurement"], result["expected_window_keys"]) == (72, 72, 60, 30)
    with pytest.raises(SystemExit):
        windows.main([*args, "--manifest", "bogus.json"])


def test_fixture_seed_and_scope_match_the_same_private_roster():
    workload = plan(50, "smoke")
    password_counter = iter(range(50))
    learners, core_seed, scope = fixture_inputs(workload, lambda: str(next(password_counter)))
    assert len(learners) == len(core_seed) == len(scope) == 50
    assert {str(subject) for subject in workload.users} == set(learners) == set(scope)
    assert all(subject.version == 4 for subject in workload.users)
    assert {row["password"] for row in learners.values()} == {str(number) for number in range(50)}
    assert all(scope_item == {"course_ids": ["backend", "backend_copy"], "tool_scopes": []}
               for scope_item in scope.values())
    assert all(learners[row["subject_ref"]]["email"] == row["email"] for row in core_seed.values())


@pytest.mark.parametrize("schema", ["a015", "a016"])
def test_stage_dispatch_uses_two_disjoint_databases_with_one_explicit_50_learner_seed(monkeypatch, schema):
    baseline = UUID("cf501596-0c95-4840-ae58-4275d7f7599d")
    shadow = UUID("ef501596-0c95-4840-ae58-4275d7f7599d")
    cluster = UUID("21955f0b-14dc-4d97-b064-dcdf31c2bbde")
    make_password = iter(str(number) for number in range(50))
    learners, seed, _ = fixture_inputs(plan(50, "full"), lambda: next(make_password))
    template = dict(owner="corp598-607-benchmark", pair=str(PAIR), learners=learners,
                    targets={"v2_only": {"instance": str(baseline)}, "shadow": {"instance": str(shadow)}})
    if schema == "a016":
        template["schema"] = schema
    private = {"windows-benchmark.template.json": template, "windows-benchmark-core-seed.json": seed}
    monkeypatch.setenv("ANALYTICS_BENCHMARK_GO", "1")
    monkeypatch.setattr(staging.local_server, "_instance_dir", lambda pair: Path(pair))
    monkeypatch.setattr(staging.local_server, "_read", lambda _folder, name, _limit: json.dumps(private[name]))
    calls = []
    monkeypatch.setattr(staging, "stage_target", lambda instance, roster, revision, resource: calls.append((instance, roster, revision, resource)))
    for phase in ("v2_only", "shadow"):
        assert staging.main(["stage", "--pair", str(PAIR), "--phase", phase, "--schema", schema,
                             "--cluster", str(cluster)]) == 0
    assert calls == [(baseline, seed, schema, cluster), (shadow, seed, schema, cluster)]


def test_r1_fixture_binds_a016_and_does_not_create_a_private_scope_file(monkeypatch):
    files = {}
    monkeypatch.setattr(staging, "prepare_folder", lambda *_: (None, Path("."), None))
    monkeypatch.setattr(staging, "_write", lambda _local, _folder, name, data: files.update({name: data}))
    assert staging.main([
        "--pair", str(PAIR), "--anchor", ANCHOR.isoformat(), "--stage", "50", "--mode", "smoke", "--schema", "a016",
        "--v2-instance", "cf501596-0c95-4840-ae58-4275d7f7599d",
        "--shadow-instance", "ef501596-0c95-4840-ae58-4275d7f7599d",
        "--course", "backend", "one", "71", "--course", "backend_copy", "two", "41",
    ]) == 0
    assert set(files) == {"windows-benchmark.template.json", "windows-benchmark-core-seed.json"}
    assert files["windows-benchmark.template.json"]["schema"] == "a016"
    assert len(files["windows-benchmark-core-seed.json"]) == 50


@pytest.mark.parametrize("enabled", [False, True])
def test_r1_core_flags_cannot_inherit_legacy_scope(enabled):
    pending = {"ANALYTICS_WINDOWS_COHORT": "synthetic-legacy-filter",
               "ANALYTICS_WINDOWS_SCOPE_FILE": "private-legacy-file",
               "ANALYTICS_WINDOWS_T0": "old-time",
               "PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "1"}
    launched = staging.release_core_env(pending, enabled, ANCHOR.isoformat())
    assert launched["PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED"] == "1"
    assert staging.core_runtime_flags(launched) == dict(
        all_eligible=enabled, intake=enabled, reconciliation=enabled,
        cohort_configured=False, scope_file_configured=False, t0=ANCHOR.isoformat() if enabled else None,
    )
    assert pending["ANALYTICS_WINDOWS_COHORT"] == "synthetic-legacy-filter"


def test_dedicated_binding_changes_only_the_exact_own_role_and_port():
    database = "analytics_live_" + PAIR.hex
    role = database + "_collector"
    url = f"postgresql://{role}:opaque@127.0.0.1:57392/{database}"
    assert staging.bind_pg_url(url, 62398, database, role) == url.replace(":57392/", ":62398/")
    assert staging.bind_pg_url(url, 62398, database, role, analytics=True) == url.replace("127.0.0.1", "[::1]")
    for unsafe in (url.replace(role, database + "_migrate"), url.replace("127.0.0.1", "192.0.2.2"),
                   url.replace("/" + database, "/another"), url + "?service=elsewhere"):
        with pytest.raises(ValueError):
            staging.bind_pg_url(unsafe, 62398, database, role)
    with pytest.raises(ValueError):
        staging.bind_pg_url(url, 62333, database, role)


def test_prepare_is_metadata_only_and_pins_owner_image_and_volume(monkeypatch):
    called = []
    project, volume = dedicated_pg.names(PAIR)
    monkeypatch.setattr(dedicated_pg, "verify_context", lambda: called.append("local context"))
    monkeypatch.setattr(dedicated_pg, "docker", lambda *args: (
        called.append(args) or json.dumps([{"Id": dedicated_pg.IMAGE}])))
    monkeypatch.setattr(dedicated_pg, "prepare_folder", lambda cluster: (None, Path("."), None))
    monkeypatch.setattr(dedicated_pg, "_write", lambda local, folder, name, value: called.append((name, value)))
    planned = dedicated_pg.prepare(PAIR)
    assert planned == dict(status="PREPARED", cluster=str(PAIR), project=project, port=62398)
    assert called[:2] == ["local context", ("image", "inspect", dedicated_pg.IMAGE)]
    owner = called[2][1]
    assert owner["volume"] == volume and owner["owner"] == str(PAIR) and len(owner["sentinel"]) == 64
    assert len(called) == 4 and called[-1][0] == "postgres.password"


def test_low_headroom_blocks_container_create_before_owner_operations(monkeypatch):
    def floor(_):
        raise base.StopRun("resource floor reached")

    monkeypatch.setattr(dedicated_pg.ResourceMonitor, "check", floor)
    monkeypatch.setattr(dedicated_pg, "docker", lambda *_: pytest.fail("no Docker change with low headroom"))
    with pytest.raises(base.StopRun, match="resource floor"):
        dedicated_pg.start(PAIR)


def test_missing_or_disabled_worker_metrics_cannot_show_shadow_ready():
    prefix = "analytics_windows_reconciliation_"
    off = prefix + "enabled 0"
    enabled = prefix + "enabled 1"
    assert worker_metrics(off, False) == dict.fromkeys(WORKER_COUNTERS, 0)
    with pytest.raises(base.StopRun, match="worker state"):
        worker_metrics(off, True)
    with pytest.raises(base.StopRun, match="observations unavailable"):
        worker_metrics(enabled, True)
    text = "\n".join([enabled, *(prefix + key + "_total 1" for key in WORKER_COUNTERS)])
    assert worker_metrics(text, True) == dict.fromkeys(WORKER_COUNTERS, 1)


def test_analytics_alias_uses_unchanged_real_local_settings():
    from pymentor_analytics.app.config import Settings, local_deletion_config

    live = staging.local_live
    owner = dict(instance_id=str(PAIR), core_instance_id=str(PAIR), dataset_run_id=str(PAIR),
                 authority_id="synthetic:" + str(PAIR))
    urls = live.build_configuration(Path("."), owner, live._pem_keys(), live._secret32(), live._secret32(),
                                    {role: f"private-{role}" for role in live.ROLES}, "synthetic",
                                    projection_revision="a014")
    name = live.database_name(str(PAIR))
    for role, raw in urls["roles"]["roles"].items():
        copy = dict(raw, database_url=staging.bind_pg_url(raw["database_url"], 62398, name, f"{name}_{role}", analytics=True))
        if "core_deletion_status_database_url" in raw:
            copy["core_deletion_status_database_url"] = staging.bind_pg_url(
                raw["core_deletion_status_database_url"], 62398, name, f"{name}_transfer_status", analytics=True
            )
        settings = Settings.model_validate(copy)
        assert "@[::1]:57392/" in settings.database_url.get_secret_value()
        local_deletion_config(settings, role)


def test_resume_cannot_start_a_container_with_a_mismatched_owner(monkeypatch):
    monkeypatch.setattr(dedicated_pg.ResourceMonitor, "check", lambda *_: None)
    monkeypatch.setattr(dedicated_pg, "read_record", lambda *_: (Path("."), {"id": "a" * 64}))
    def owner_mismatch(record, *, running):
        assert running is False
        raise ValueError("wrong owner")

    monkeypatch.setattr(dedicated_pg, "verify_container", owner_mismatch)
    monkeypatch.setattr(dedicated_pg, "docker", lambda *_: pytest.fail("no start after owner mismatch"))
    with pytest.raises(ValueError, match="wrong owner"):
        dedicated_pg.resume(PAIR)



def test_capture_requires_real_pool_size_max_and_overflow_gauges():
    from benchmark_windows_target import measured_pool

    text = "\n".join(
        f'analytics_database_pool_{key}{suffix}{{role="query"}} {number}'
        for key, value in {"size": 2, "max": 2, "overflow": 0}.items()
        for suffix, number in (("_known", 1), ("", value))
    )
    assert measured_pool(text) == {"size": 2, "max": 2, "overflow": 0}
    with pytest.raises(base.StopRun):
        measured_pool(text.replace('max_known{role="query"} 1', 'max_known{role="query"} 0'))


def test_scheduled_empty_query_accepts_null_as_of_but_nonempty_does_not():
    empty = report(0).model_copy(update={
        "as_of": None,
        "totals": report(0).totals.model_copy(update={"unique_visitors": 0}),
        "daily": [],
    })
    assert windows.totals(empty) == dict(unique_visitors=0, active_ms=0, theory_ms=0, practice_ms=0)
    positive = report(60_000).model_copy(update={"as_of": None})
    with pytest.raises(base.ObservedViolation, match="nonempty report"):
        windows.totals(positive)