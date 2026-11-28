"""Live Core -> Collector -> PostgreSQL contract checks for a dedicated synthetic target."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from tools.bootstrap_postgres import config


ROUTE = "/api/analytics/windows/batch"
GRANT = "/api/analytics/windows/grant"
DB_PATTERN = re.compile(r"analytics_(?:test|live)_[0-9a-f]{32}\Z")


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_WINDOWS_LIVE_TARGET") != "1":
        pytest.skip("explicit externally managed synthetic Core/Collector target required")
    setup = json.loads(Path(os.environ["CORP605_HTTP_TARGET_FILE"]).read_text(encoding="utf-8"))
    bootstrap = config(Path(os.environ["CORP605_PG_BOOTSTRAP_FILE"]))
    parsed = urlsplit(setup["core_origin"])
    if (setup["synthetic"] is not True or not DB_PATTERN.fullmatch(setup["analytics_db"])
            or parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or setup["core_origin"] != f"http://127.0.0.1:{parsed.port}"
            or str(UUID(setup["subject_ref"])) != setup["subject_ref"]):
        raise ValueError("isolated Analytics HTTP target required")
    with connect(bootstrap, setup) as conn:
        schema = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
        assert schema == ("a015",)
    return bootstrap, setup


@contextmanager
def connect(bootstrap, setup):
    name = setup["analytics_db"]
    with psycopg.connect(host=bootstrap["host"], port=bootstrap["port"], dbname=name,
                         user=bootstrap["user"], password=bootstrap["password"], connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        owner = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)).fetchone()
        marker = conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchone()
        if owner != (name + "_migrate",) or marker != (setup["sentinel"],):
            raise ValueError("synthetic database owner or marker mismatch")
        yield conn


def window_rows(bootstrap, setup, key):
    with connect(bootstrap, setup) as conn:
        return conn.execute("""SELECT w.stats, w.revision, c.payload_hash
            FROM analytics.student_activity_windows w JOIN analytics.student_activity_window_control c
            USING (student_id,activity_date,slot_no)
            WHERE w.student_id=%s AND w.activity_date=%s AND w.slot_no=%s""",
            (UUID(setup["subject_ref"]), date.fromisoformat(key["activity_date"]), key["slot_no"])).fetchall()


def open_grant(client, writer_id):
    reply = client.post(GRANT, json={"protocol_version": 1, "writer_id": writer_id, "writer_generation": 1})
    assert reply.status_code == 200
    return reply.json()


def sign_in(client, setup):
    reply = client.post("/api/user/login", json={"email": setup["email"], "password": setup["password"]})
    assert reply.status_code == 200 and client.cookies


def test_live_round_trip_commit_and_duplicate(target):
    bootstrap, setup = target
    moment = datetime.now(timezone(timedelta(hours=3))) - timedelta(minutes=30)
    writer = str(uuid4())
    key = {"activity_date": moment.date().isoformat(), "slot_no": moment.hour * 4 + moment.minute // 15 + 1}
    course, lesson, version = setup["course_id"], setup["lesson_id"], setup["catalog_version"]
    expected = [{"tool_id": "practice", "course_id": course, "lesson_id": lesson, "active_ms": 360000},
                {"tool_id": "theory", "course_id": course, "lesson_id": lesson, "active_ms": 240000}]
    snapshot = {**key, "writer_id": writer, "writer_generation": 1, "server_epoch": 1, "seq": 1,
                "stats": expected[::-1], "catalog_versions": {course: version}, "client_final": False}
    raw = json.dumps({"protocol_version": 1, "windows": [snapshot]}, separators=(",", ":")).encode()
    assert window_rows(bootstrap, setup, key) == []
    with httpx.Client(base_url=setup["core_origin"], timeout=10, trust_env=False) as client:
        sign_in(client, setup)
        grant = open_grant(client, writer)
        assert grant["subject_ref"] == setup["subject_ref"]
        headers = {"Authorization": "Bearer " + grant["grant"], "Content-Type": "application/json"}
        first = client.post(ROUTE, content=raw, headers=headers)
        assert first.status_code == 200
        ack = first.json()["items"][0]
        assert (ack["status"], ack["writer_id"], ack["seq"]) == ("committed", writer, 1)
        observed = [(expected, ack["confirmed"]["revision"], ack["payload_hash"])]
        assert window_rows(bootstrap, setup, key) == observed
        second = client.post(ROUTE, content=raw, headers=headers)
        assert second.status_code == 200
        duplicate = second.json()["items"][0]
        assert duplicate["status"] == "duplicate" and duplicate["confirmed"] == ack["confirmed"]
        assert window_rows(bootstrap, setup, key) == observed


@contextmanager
def collector_listener(app):
    import socket
    import threading
    import time
    import uvicorn

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, http="h11", ws="none", access_log=False,
                                              proxy_headers=False, log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        try:
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.02)
            assert server.started
            yield port
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


@pytest.mark.parametrize("scenario", ["initial", "partial", "a014-off", "a014-on", "a015-off", "a015-on", "worker"])
def test_signed_gateway_commit_and_privacy(tmp_path, monkeypatch, scenario):
    """Run real Core routing/signing, Collector listener, signed authority and a015.

    Synthetic Core session resolution and educational catalogs are fixture inputs.
    """
    import asyncio
    from copy import deepcopy
    import runpy
    import time

    from fastapi.testclient import TestClient

    from pymentor_analytics.adapters.postgres.migrate import upgrade
    from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.factory import create_app
    from pymentor_analytics.app.privacy import PrivacyRuntime
    from tools.bootstrap_postgres import cleanup, connect as setup_pg, create

    if os.environ.get("ANALYTICS_RUN_WINDOWS_HTTP_PG") != "1":
        pytest.skip("explicit synthetic signed HTTP/a015 opt-in required")
    analytics = Path(__file__).resolve().parents[2]
    root = analytics.parents[1]
    monkeypatch.syspath_prepend(str(root / "services/core-api"))
    from pymentor_core.adapters.analytics_http.http import request as actual_forward
    from pymentor_core.app import analytics_windows_gateway as gateway
    core = runpy.run_path(str(root / "services/core-api/tests/analytics_delivery/test_windows_gateway.py"))
    helpers = runpy.run_path(str(analytics / "tests/privacy_integration/test_signed_deletion_pipeline.py"))
    bootstrap = config(Path(os.environ["CORP605_PG_BOOTSTRAP_FILE"]))
    schema = scenario.split("-")[0] if scenario.startswith("a0") else "a015"
    name, sentinel, urls = create(bootstrap, revision=schema)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision=schema)
        with setup_pg(bootstrap, name) as conn:
            actual_schema = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
            version = conn.execute("SHOW server_version").fetchone()[0]
            assert actual_schema == (schema,)
        print(f"605.B target={name} schema={actual_schema[0]} PostgreSQL={version}")
        sample = runpy.run_path(str(analytics / "tests/student_windows/test_http.py"))["sample"].__wrapped__()
        snapshot = sample["windows"][0]
        snapshot["stats"][0].update(course_id="backend", lesson_id="lesson-1")
        snapshot["stats"][1].update(course_id="sql", lesson_id="lesson-1")
        snapshot["catalog_versions"] = {"backend": "backend-v1", "sql": "sql-v1"}
        fixture_catalog = json.dumps([
            {"schema_version": 2, "kind": "catalog_snapshot", "control_id": str(uuid4()),
             "course_id": course, "catalog_version": version, "track_id": "synthetic-track",
             "lessons": [{"lesson_id": "lesson-1"}]}
            for course, version in snapshot["catalog_versions"].items()
        ])
        with helpers["authority_server"](tmp_path) as (authority, raw_authority, manifest, producer, command, _):
            async def run_privacy():
                database = helpers["database"](urls["privacy"])
                runtime = PrivacyRuntime(AuthorityClient(authority), database, store=PostgresPrivacyStore(database))
                try:
                    await helpers["observe"](runtime)
                    assert await runtime.is_ready()
                finally:
                    await database.close()
            asyncio.run(run_privacy())

            app, scope, _, _, private_key, env = core["runtime"].__wrapped__(tmp_path, monkeypatch)
            ring = json.loads(manifest)
            ring["grant"] = {"issuer": "core-test", "keys": {"k": helpers["pem"](private_key)}}
            settings = Settings(
                database_url=urls["collector"], trust_manifest_json=json.dumps(ring),
                privacy_authority_json=raw_authority, fixture_catalog_json=fixture_catalog,
                windows_intake_enabled=not scenario.endswith("-off"), windows_cohort=core["SUBJECT"],
                windows_t0=datetime.now(timezone.utc) - timedelta(hours=6),
            )
            collector = create_app("collector", settings)
            target = {"subject_ref": core["SUBJECT"], "analytics_db": name, "sentinel": sentinel}
            confirmed_stats = sorted(snapshot["stats"], key=lambda row: (row["tool_id"], row["course_id"], row["lesson_id"]))
            if schema == "a015":
                assert window_rows(bootstrap, target, snapshot) == []
            with collector_listener(collector) as port:
                origin = f"http://127.0.0.1:{port}"
                app.state.analytics_windows_gateway = gateway.configure({**env, "ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN": origin})
                monkeypatch.setattr(gateway, "http_request", actual_forward)
                with httpx.Client(base_url=origin, timeout=3, trust_env=False) as listener, TestClient(
                        app, cookies={"session": "synthetic"}) as client:
                    deadline = time.monotonic() + 8
                    while listener.get("/ready").status_code != 200 and time.monotonic() < deadline:
                        time.sleep(.05)
                    assert listener.get("/ready").status_code == 200
                    if scenario.startswith("a0"):
                        check_compatibility(bootstrap, target, client, listener, app, sample, core, scenario)
                        return
                    if scenario == "worker":
                        check_worker_runtime(bootstrap, target, client, listener, app, sample, core, settings,
                                             urls["processor"], tmp_path)
                        return
                    if scenario == "partial":
                        check_partial_and_purge(bootstrap, target, client, listener, collector, sample, core,
                                                authority, producer, command, helpers, run_privacy, urls["processor"])
                        return
                    grant = open_grant(client, core["WRITER"])
                    assert grant["subject_ref"] == core["SUBJECT"]
                    headers = {"Authorization": "Bearer " + grant["grant"]}
                    first = client.post(ROUTE, json=sample, headers=headers)
                    assert first.status_code == 200 and "items" in first.json(), first.json().get("code")
                    ack = first.json()["items"][0]
                    assert (ack["index"], ack["writer_id"], ack["seq"], ack["status"]) == (0, core["WRITER"], 1, "committed")
                    assert ack["confirmed"]["revision"] == 1
                    expected = [(confirmed_stats, 1, ack["payload_hash"])]
                    assert window_rows(bootstrap, target, snapshot) == expected

                    duplicate = client.post(ROUTE, json=sample, headers=headers).json()["items"][0]
                    assert duplicate["status"] == "duplicate" and duplicate["confirmed"] == ack["confirmed"]
                    assert window_rows(bootstrap, target, snapshot) == expected
                    print("605.B committed then duplicate: revision=1 active_ms=600000 one row")

                    foreign = deepcopy(sample)
                    foreign["windows"][0]["subject_ref"] = str(uuid4())
                    denied_foreign = client.post(ROUTE, json=foreign, headers=headers).json()["items"][0]
                    assert denied_foreign["code"] == "subject_forbidden" and denied_foreign["confirmed"] is None
                    assert window_rows(bootstrap, target, snapshot) == expected

                    scope(["backend"], platform=True)
                    change = deepcopy(sample)
                    change["windows"][0]["seq"] = 2
                    changed = client.post(ROUTE, json=change, headers=headers).json()["items"][0]
                    assert changed["code"] == "course_forbidden" and changed["confirmed"] is None
                    fresh = open_grant(client, core["WRITER"])
                    fresh_headers = {"Authorization": "Bearer " + fresh["grant"]}
                    fresh_denied = client.post(ROUTE, json=change, headers=fresh_headers).json()["items"][0]
                    assert fresh_denied["code"] == "course_forbidden"
                    assert window_rows(bootstrap, target, snapshot) == expected
                    print("605.B foreign lifecycle and withdrawn course denied; original stats, hash and revision retained")

                    tool = {**snapshot, "slot_no": snapshot["slot_no"] + (1 if snapshot["slot_no"] < 96 else -1),
                            "stats": [{"tool_id": "catalog", "course_id": None, "lesson_id": None, "active_ms": 30000}],
                            "catalog_versions": {}}
                    tool_body = {"protocol_version": 1, "windows": [tool]}
                    tool_ack = client.post(ROUTE, json=tool_body, headers=fresh_headers).json()["items"][0]
                    assert tool_ack["status"] == "committed"
                    tool_rows = [(tool["stats"], 1, tool_ack["payload_hash"])]
                    assert window_rows(bootstrap, target, tool) == tool_rows
                    print("605.B scoped catalog/null tool committed: revision=1 active_ms=30000")

                    unharmed = {**tool, "seq": 2, "stats": [dict(tool["stats"][0], active_ms=60000)]}
                    query = create_app("query", Settings(database_url=urls["query"],
                        trust_manifest_json=settings.trust_manifest_json, privacy_authority_json=raw_authority))
                    with TestClient(query) as reports:
                        check_privacy_closure(bootstrap, target, listener, reports, client,
                            app.state.analytics_grant_signer, unharmed, fresh_headers, tool_rows,
                            blocked=lambda: publish_control(authority, producer, command, helpers),
                            recovered=lambda: asyncio.run(run_privacy()))
                    assert window_rows(bootstrap, target, snapshot) == expected
    finally:
        cleanup(bootstrap, name, sentinel, revision=schema)
        with setup_pg(bootstrap) as conn:
            assert not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchall()
            assert not conn.execute("SELECT 1 FROM pg_roles WHERE left(rolname, length(%s))=%s", (name, name)).fetchall()
        print(f"605.B target={name} sentinel/owner cleanup=confirmed")


def publish_control(authority, producer, command, helpers):
    import asyncio
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.contracts.v2 import DeletionFence

    deletion = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                             subject_ref=uuid4(), epoch=1, occurred_at=datetime.now(timezone.utc))
    append = AuthorityClient(authority).append(deletion, producer="synthetic",
        producer_token=helpers["producer_token"](producer), command_key=command)
    assert asyncio.run(append).seq == 1


def check_privacy_closure(bootstrap, target, collector, query, core, signer, window, windows_headers, before,
                          *, blocked, recovered):
    import time

    def wait_for_gate(status):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if collector.get("/ready").status_code == status and query.get("/ready").status_code == status:
                return
            time.sleep(.05)
        pytest.fail(f"Collector and Query did not reach readiness {status}")

    def authorization(scope):
        token = signer.grant(subject=target["subject_ref"], role="user", scope=scope,
                             courses=["backend"], subjects=[], global_read=False)
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def event_count(event):
        with connect(bootstrap, target) as pg:
            return pg.execute("SELECT count(*) FROM analytics.activity_facts WHERE event_id=%s",
                              (UUID(event["event_id"]),)).fetchone()[0]

    def v2_write(raw):
        return collector.post("/api/analytics/events/batch", content=raw, headers=write_headers)

    def v2_read():
        return query.get("/api/analytics/reports/activity", params=params, headers=read_headers)

    def v3_write():
        return core.post(ROUTE, content=window_raw, headers=windows_headers)

    fixture = Path(__file__).resolve().parents[2] / "contracts/fixtures/browser-batch.json"
    browser = json.loads(fixture.read_text(encoding="utf-8"))["events"][1]
    latest = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
    first_start = latest - timedelta(seconds=70)
    first_end = first_start + timedelta(seconds=30)
    first = dict(browser, event_id=str(uuid4()), course_id="backend", catalog_version="backend-v1",
                 active_ms=30000, started_at=first_start.isoformat(), ended_at=first_end.isoformat(),
                 last_action_at=first_start.isoformat())
    next_start = latest - timedelta(seconds=30)
    pending = dict(first, event_id=str(uuid4()), started_at=next_start.isoformat(), ended_at=latest.isoformat(),
                   last_action_at=next_start.isoformat())
    first_raw = json.dumps({"events": [first]}, separators=(",", ":")).encode()
    pending_raw = json.dumps({"events": [pending]}, separators=(",", ":")).encode()
    window_raw = json.dumps({"protocol_version": 1, "windows": [window]}, separators=(",", ":")).encode()
    windows_headers = {**windows_headers, "Content-Type": "application/json"}
    write_headers, read_headers = authorization("collect"), authorization("report:read")
    params = {"from_time": (first_start - timedelta(seconds=1)).isoformat(),
              "to_time": (latest + timedelta(seconds=1)).isoformat(), "course_id": "backend"}

    wait_for_gate(200)
    initial_write = v2_write(first_raw)
    assert initial_write.status_code == 200 and initial_write.json()["items"][0]["status"] == "accepted"
    initial_read = v2_read()
    assert initial_read.status_code == 200 and initial_read.json()["totals"]["active_ms"] == 30000
    assert (event_count(first), event_count(pending)) == (1, 0)

    blocked()
    wait_for_gate(503)
    closed_v2, closed_query, closed_v3 = v2_write(pending_raw), v2_read(), v3_write()
    assert (closed_v2.status_code, closed_query.status_code, closed_v3.status_code) == (503, 503, 503)
    assert (closed_v2.json()["code"], closed_query.json()["code"], closed_v3.json()["code"]) == (
        "unavailable", "unavailable", "privacy_unavailable")
    assert "totals" not in closed_query.json()
    assert (event_count(first), event_count(pending)) == (1, 0)
    assert window_rows(bootstrap, target, window) == before

    recovered()
    wait_for_gate(200)
    restored_read = v2_read()
    assert restored_read.status_code == 200 and restored_read.json()["totals"] == initial_read.json()["totals"]
    for expected in ("accepted", "duplicate"):
        response = v2_write(pending_raw)
        assert response.status_code == 200 and response.json()["items"][0]["status"] == expected
    assert event_count(pending) == 1
    final_read = v2_read()
    assert final_read.status_code == 200 and final_read.json()["totals"]["active_ms"] == 60000
    for expected in ("committed", "duplicate"):
        response = v3_write()
        assert response.status_code == 200 and response.json()["items"][0]["status"] == expected
    ack = response.json()["items"][0]
    assert window_rows(bootstrap, target, window) == [(window["stats"], before[0][1] + 1, ack["payload_hash"])]
    print("607 Privacy HWM ahead: v2 collect, v2 report and v3 write unavailable; replay reopens and retries once")


def check_compatibility(bootstrap, target, client, listener, app, sample, core, scenario):
    with connect(bootstrap, target) as pg:
        assert pg.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() == (0,)
    signer = app.state.analytics_grant_signer
    legacy_token = signer.grant(subject=core["SUBJECT"], role="user", scope="collect",
                                courses=["backend"], subjects=[], global_read=False)
    events = Path(__file__).resolve().parents[2] / "contracts/fixtures/browser-batch.json"
    event = json.loads(events.read_text(encoding="utf-8"))["events"][1]
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
    start = end - timedelta(seconds=30)
    event.update(event_id=str(uuid4()), course_id="backend", catalog_version="backend-v1",
                 active_ms=30000, started_at=start.isoformat(), ended_at=end.isoformat(), last_action_at=start.isoformat())
    old = listener.post("/api/analytics/events/batch", json={"events": [event]},
                        headers={"Authorization": "Bearer " + legacy_token})
    assert old.status_code == 200 and old.json()["items"][0]["status"] == "accepted"
    grant = open_grant(client, core["WRITER"])
    current = client.post(ROUTE, json=sample, headers={"Authorization": "Bearer " + grant["grant"]})
    if scenario in {"a014-off", "a015-off"}:
        assert current.status_code == 404
    elif scenario == "a014-on":
        assert current.status_code == 503 and current.json()["code"] == "unavailable"
    else:
        assert current.status_code == 200 and current.json()["items"][0]["status"] == "committed"
    with connect(bootstrap, target) as pg:
        assert pg.execute("SELECT count(*) FROM analytics.activity_facts WHERE event_id=%s",
                          (UUID(event["event_id"]),)).fetchone() == (1,)
        table = pg.execute("SELECT to_regclass('analytics.student_activity_windows') IS NOT NULL").fetchone()
        assert table == (scenario.startswith("a015"),)
        if table[0]:
            count, = pg.execute("SELECT count(*) FROM analytics.student_activity_windows").fetchone()
            assert count == (1 if scenario == "a015-on" else 0)
    print(f"605.C compatibility {scenario}: v2 200/accepted, v3 {current.status_code}")


def check_partial_and_purge(bootstrap, target, client, listener, collector, sample, core, authority,
                            producer, command, helpers, run_privacy, processor_url):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    import threading
    import time

    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.contracts.v2 import DeletionFence
    from pymentor_analytics.adapters.postgres.student_windows import PostgresStudentWindows
    from pymentor_analytics.app.privacy import PrivacyRuntime
    from pymentor_analytics.contracts.windows_v1 import ReconcileDecision, WindowKey
    from pymentor_analytics.modules.collector.ports import IntakeUnavailable

    def post(batch, token):
        wire = json.dumps(batch, separators=(",", ":")).encode()
        return client.post(ROUTE, content=wire,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})

    store = collector.state.windows_store
    original = store.write_window
    first, second = sample["windows"][0], deepcopy(sample["windows"][0])
    second["slot_no"] += 1 if second["slot_no"] < 96 else -1
    sample["windows"].append(second)
    writer_a = open_grant(client, core["WRITER"])["grant"]

    async def lose_ack(key, snapshot, *, received_at):
        await original(key, snapshot, received_at=received_at)
        raise IntakeUnavailable("controlled post-commit response loss")

    store.write_window = lose_ack
    try:
        lost = post(sample, writer_a)
    finally:
        store.write_window = original
    assert lost.status_code == 503 and "items" not in lost.json()
    first_rows = window_rows(bootstrap, target, first)
    assert len(first_rows) == 1 and first_rows[0][1] == 1
    assert window_rows(bootstrap, target, second) == []
    replay = post(sample, writer_a)
    assert replay.status_code == 200
    returned = replay.json()["items"]
    assert [(item["index"], item["status"]) for item in returned] == [(0, "duplicate"), (1, "committed")]
    assert window_rows(bootstrap, target, first) == first_rows
    assert len(window_rows(bootstrap, target, second)) == 1
    print("605.C partial commit with lost ACK -> 503 -> duplicate + committed; two PG rows")

    newer = deepcopy(first)
    newer["seq"] = 4
    newer["stats"][0]["active_ms"] += 12000
    fourth = {"protocol_version": 1, "windows": [newer]}
    earlier = {"protocol_version": 1, "windows": [dict(first, seq=3)]}
    assert post(fourth, writer_a).json()["items"][0]["status"] == "committed"
    updated = window_rows(bootstrap, target, first)
    assert updated[0][1] == 2
    assert post(earlier, writer_a).json()["items"][0]["code"] == "stale_seq"
    assert post(fourth, writer_a).json()["items"][0]["status"] == "duplicate"
    different = deepcopy(fourth)
    different["windows"][0]["stats"][0]["active_ms"] += 1000
    assert post(different, writer_a).json()["items"][0]["code"] == "payload_conflict"
    assert window_rows(bootstrap, target, first) == updated

    writer_b_id = str(uuid4())
    writer_b = open_grant(client, writer_b_id)["grant"]
    contested = {"protocol_version": 1, "windows": [dict(newer, writer_id=writer_b_id, seq=100)]}
    pending = post(contested, writer_b).json()["items"][0]
    assert (pending["status"], pending["code"]) == ("pending", "ownership_conflict")
    with connect(bootstrap, target) as pg:
        phase = pg.execute("""SELECT phase,coverage_reasons FROM analytics.student_activity_window_control
            WHERE student_id=%s AND activity_date=%s AND slot_no=%s""",
            (UUID(target["subject_ref"]), date.fromisoformat(first["activity_date"]), first["slot_no"])).fetchone()
    assert phase[0] == "pending_reconciliation" and "writer_conflict" in phase[1]
    assert window_rows(bootstrap, target, first) == updated
    print("605.C seq 4/3/4+changed conflict; second writer pending without changing business row")

    key = WindowKey.model_validate_json(json.dumps({
        "student_id": target["subject_ref"], "activity_date": first["activity_date"], "slot_no": first["slot_no"],
    }))

    async def repair():
        repair_db = helpers["database"](processor_url)
        gate = PrivacyRuntime(AuthorityClient(authority), repair_db)
        repair_db.transaction_guard = gate.check_in_transaction
        try:
            await helpers["observe"](gate)
            worker_store = PostgresStudentWindows(repair_db, cohort=[key.student_id])
            cut = await worker_store.read_reconciliation(key)
            previous = cut.state
            stats = list(previous.confirmed.stats)
            stats[0] = stats[0].model_copy(update={"active_ms": stats[0].active_ms - 1000})
            decision = ReconcileDecision(previous.source_generation, previous.change_generation,
                                         tuple(stats), previous.catalog_versions)
            repaired = await worker_store.reconcile_window(key, decision, rebase_at=datetime.now(timezone.utc))
            assert repaired.confirmed.server_epoch == 2
            return repaired.confirmed.revision
        finally:
            await repair_db.close()

    revision = asyncio.run(repair())
    after_repair = window_rows(bootstrap, target, first)
    stale_epoch = post({"protocol_version": 1, "windows": [dict(newer, seq=100)]}, writer_a).json()["items"][0]
    assert stale_epoch["code"] == "stale_epoch" and after_repair[0][1] == revision
    assert window_rows(bootstrap, target, first) == after_repair
    print(f"605.C repair revision={revision}; old epoch seq=100 refused without mutation")

    entered, released = threading.Event(), threading.Event()
    store = collector.state.windows_store
    save = store._save

    async def hold_after_write(session, key, previous, state):
        await save(session, key, previous, state)
        entered.set()
        if not await asyncio.to_thread(released.wait, 5):
            raise AssertionError("did not release before-commit fixture")

    # Pause after uncommitted DML so only the production guard can block commit.
    store._save = hold_after_write
    renew = {"protocol_version": 1, "windows": [dict(newer, server_epoch=2, seq=1)]}
    try:
        with ThreadPoolExecutor(max_workers=1) as thread:
            result = thread.submit(post, renew, writer_a)
            try:
                assert entered.wait(5)
                delete = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                                       subject_ref=UUID(target["subject_ref"]), epoch=1, occurred_at=datetime.now(timezone.utc))
                append = AuthorityClient(authority).append(delete, producer="synthetic",
                         producer_token=helpers["producer_token"](producer), command_key=command)
                assert asyncio.run(append).seq == 1
                deadline = time.monotonic() + 4
                while listener.get("/ready").status_code != 503 and time.monotonic() < deadline:
                    time.sleep(.02)
                assert listener.get("/ready").status_code == 503
            finally:
                released.set()
            blocked = result.result(timeout=10)
            assert blocked.status_code == 503 and blocked.json()["code"] == "privacy_unavailable"
    finally:
        store._save = save
    assert window_rows(bootstrap, target, first) == after_repair
    asyncio.run(run_privacy())
    deadline = time.monotonic() + 8
    while listener.get("/ready").status_code != 200 and time.monotonic() < deadline:
        time.sleep(.05)
    assert listener.get("/ready").status_code == 200
    assert post(sample, writer_a).status_code == 403
    assert window_rows(bootstrap, target, first) == window_rows(bootstrap, target, second) == []
    with connect(bootstrap, target) as pg:
        rows = pg.execute("SELECT count(*) FROM analytics.student_activity_window_control WHERE student_id=%s",
                          (UUID(target["subject_ref"]),)).fetchone()
        assert rows == (0,)
    print("605.C signed deletion while v3 paused before commit -> unavailable; purge -> 403 and zero business/control rows")


def check_worker_runtime(bootstrap, target, client, listener, app, sample, core, settings, processor_url, folder):
    from copy import deepcopy
    import time
    from fastapi.testclient import TestClient

    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.factory import create_app
    from pymentor_analytics.contracts.windows_v1 import WindowCoordinate, window_bounds

    learner = target["subject_ref"]
    grant = open_grant(client, core["WRITER"])["grant"]
    headers = {"Authorization": "Bearer " + grant}
    signer = app.state.analytics_grant_signer
    v2_headers = {"Authorization": "Bearer " + signer.grant(subject=learner, role="user", scope="collect",
                                                            courses=["backend"], subjects=[], global_read=False)}
    seed = sample["windows"][0]
    old_event = Path(__file__).resolve().parents[2] / "contracts/fixtures/browser-batch.json"
    stable = json.loads(old_event.read_text(encoding="utf-8"))["events"][1]
    expected_stats = [{"tool_id": "theory", "course_id": "backend", "lesson_id": "lesson-1", "active_ms": 60000}]
    incorrect_stats = [dict(expected_stats[0], active_ms=120000)]

    def row(offset):
        anchor = datetime.now(timezone(timedelta(hours=3))) - timedelta(minutes=30 + 15 * offset)
        result = deepcopy(seed)
        result.update(activity_date=anchor.date().isoformat(), slot_no=anchor.hour * 4 + anchor.minute // 15 + 1,
                      stats=incorrect_stats, catalog_versions={"backend": "backend-v1"}, client_final=True)
        return result

    def ingest(key, *, v2_client=listener, send_window=True):
        if send_window:
            saved = client.post(ROUTE, json={"protocol_version": 1, "windows": [key]}, headers=headers)
            assert saved.status_code == 200 and saved.json()["items"][0]["status"] == "committed"
        coordinate = WindowCoordinate.model_validate_json(json.dumps({
            "activity_date": key["activity_date"], "slot_no": key["slot_no"],
        }))
        began = window_bounds(coordinate)[0] + timedelta(seconds=1)
        ended = began + timedelta(seconds=60)
        event = dict(stable, event_id=str(uuid4()), course_id="backend", catalog_version="backend-v1",
                     section="theory", started_at=began.isoformat(), ended_at=ended.isoformat(),
                     last_action_at=began.isoformat())
        old = v2_client.post("/api/analytics/events/batch", json={"events": [event]}, headers=v2_headers)
        assert old.status_code == 200 and old.json()["items"][0]["status"] == "accepted"

    def stored(key):
        with connect(bootstrap, target) as pg:
            result = pg.execute("""SELECT w.stats,w.revision,c.phase,c.server_epoch,c.reconciliation_evidence
                FROM analytics.student_activity_window_control c
                LEFT JOIN analytics.student_activity_windows w USING (student_id,activity_date,slot_no)
                WHERE student_id=%s AND activity_date=%s AND slot_no=%s""",
                (UUID(learner), date.fromisoformat(key["activity_date"]), key["slot_no"])).fetchone()
            assert result is not None
            return result

    def processor(*, enabled, manifest=None):
        return create_app("processor", Settings(
            database_url=processor_url, trust_manifest_json=settings.trust_manifest_json,
            privacy_authority_json=settings.privacy_authority_json, processor_generic_worker_enabled=False,
            windows_reconciliation_enabled=enabled, windows_cohort=settings.windows_cohort,
            windows_t0=settings.windows_t0, windows_reconciliation_proof_file=manifest,
        ))

    one, unknown, catchup = (row(offset) for offset in (0, 1, 2))
    ingest(one)
    ingest(unknown)
    disabled = processor(enabled=False)
    with TestClient(disabled) as proc:
        metrics = proc.get("/metrics").text
        assert "analytics_windows_reconciliation_enabled 0" in metrics
        assert stored(one)[:4] == (incorrect_stats, 1, "pending_reconciliation", 1)
        assert stored(unknown)[:4] == (incorrect_stats, 1, "pending_reconciliation", 1)
    print("605.W off: valid a015 and Privacy; pending stored without repair, v2 kept accepting")

    manifest = folder / "trusted-window-coverage.json"

    def independently_expected(key):
        return {"key": {"student_id": learner, "activity_date": key["activity_date"], "slot_no": key["slot_no"]},
                "expected_stats": expected_stats, "v2_source_complete": True}

    def publish(*keys):
        manifest.write_text(json.dumps({
            "protocol_version": 1, "cohort_t0": settings.windows_t0.isoformat(),
            "windows": [independently_expected(key) for key in keys],
        }), encoding="utf-8")

    def poll(key, phase):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = stored(key)
            if current[2] == phase:
                return current
            time.sleep(.06)
        raise AssertionError(f"worker did not reach {phase}: {stored(key)[2]}")

    publish(one)
    started = processor(enabled=True, manifest=manifest)
    with TestClient(started) as proc:
        runtime = started.state.windows_runtime
        recorded = poll(one, "repaired")
        assert recorded[:4] == (expected_stats, 2, "repaired", 2)
        evidence = recorded[4]
        assert evidence["coverage_source"] == "managed_manifest"
        assert evidence["before"]["context_diff"] and not evidence["after"]["context_diff"]
        assert poll(unknown, "incomplete_coverage")[:4] == (incorrect_stats, 1, "incomplete_coverage", 1)
        metrics = proc.get("/metrics").text
        assert "analytics_windows_reconciliation_enabled 1" in metrics
        assert "analytics_windows_reconciliation_repairs_total 1" in metrics
        assert "analytics_windows_reconciliation_coverage_unknown_total 1" in metrics
        assert learner not in metrics
    assert runtime._task is None
    print("605.W on: trusted window 120000->60000, revision 1->2, mismatch 1->0; untrusted window unknown without changing stats")

    intake_off = create_app("collector", settings.model_copy(update={"windows_intake_enabled": False}))
    with TestClient(intake_off) as off:
        deadline = time.monotonic() + 8
        while off.get("/ready").status_code != 200 and time.monotonic() < deadline:
            time.sleep(.05)
        assert off.post(ROUTE, json={"protocol_version": 1, "windows": [catchup]}, headers=headers).status_code == 404
        ingest(catchup, v2_client=off, send_window=False)
    assert window_rows(bootstrap, target, catchup) == []
    with connect(bootstrap, target) as pg:
        pending = pg.execute("""SELECT phase,source_generation FROM analytics.student_activity_window_control
            WHERE student_id=%s AND activity_date=%s AND slot_no=%s""",
            (UUID(learner), date.fromisoformat(catchup["activity_date"]), catchup["slot_no"])).fetchone()
        assert pending == ("pending_reconciliation", 1)
    publish(one, catchup)
    resumed = processor(enabled=True, manifest=manifest)
    with TestClient(resumed):
        runtime = resumed.state.windows_runtime
        assert poll(catchup, "repaired")[:4] == (expected_stats, 1, "repaired", 2)
        assert stored(one) == recorded
    assert runtime._task is None
    print("605.W stop/restart: pending key caught up, first key retains its exact revision and evidence")


@pytest.mark.parametrize("revision", ["a014", "a015"])
def test_exact_baseline_collector_and_report_on_both_schemas(tmp_path, revision):
    """Execute the owning baseline source in a separate Python process."""
    import asyncio
    import runpy
    import subprocess
    import sys
    import zipfile

    from pymentor_analytics.adapters.postgres.migrate import upgrade
    from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.privacy import PrivacyRuntime
    from tools.bootstrap_postgres import cleanup, connect as setup_pg, create

    if os.environ.get("ANALYTICS_RUN_WINDOWS_HTTP_PG") != "1":
        pytest.skip("explicit synthetic signed HTTP/mixed-schema opt-in required")
    analytics = Path(__file__).resolve().parents[2]
    root = analytics.parents[1]
    base = "399df8548093609eaf168441be8679c8b6d80b37"
    archive = tmp_path / "baseline.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--output={archive}", base,
                    "services/analytics/pymentor_analytics"], cwd=root, timeout=15, check=True)
    old_root = tmp_path / "old"
    with zipfile.ZipFile(archive) as package:
        package.extractall(old_root)
    helpers = runpy.run_path(str(analytics / "tests/privacy_integration/test_signed_deletion_pipeline.py"))
    data = config(Path(os.environ["CORP605_PG_BOOTSTRAP_FILE"]))
    name, sentinel, urls = create(data, revision=revision)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision=revision)
        with helpers["authority_server"](tmp_path) as (authority, authority_json, trust, _, _, grant_key):
            async def init_privacy():
                database = helpers["database"](urls["privacy"])
                runtime = PrivacyRuntime(AuthorityClient(authority), database, store=PostgresPrivacyStore(database))
                try:
                    await helpers["observe"](runtime)
                    assert await runtime.is_ready()
                finally:
                    await database.close()
            asyncio.run(init_privacy())

            subject = uuid4()
            import jwt
            import time
            now = int(time.time())
            report = jwt.encode({"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": str(subject),
                                 "iat": now, "nbf": now, "exp": now+60, "role": "user", "scope": "report:read",
                                 "course_ids": ["backend"], "subject_refs": [], "global_read": False},
                                grant_key, algorithm="EdDSA", headers={"kid": "g"})
            browser = json.loads((analytics / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][1]
            end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
            start = end - timedelta(seconds=60)
            browser.update(event_id=str(uuid4()), course_id="backend", started_at=start.isoformat(),
                           ended_at=end.isoformat(), last_action_at=start.isoformat())
            catalog = [{"schema_version": 2, "kind": "catalog_snapshot", "control_id": str(uuid4()),
                        "course_id": "backend", "catalog_version": "catalog-1", "track_id": "synthetic-track",
                        "lessons": [{"lesson_id": browser["lesson_id"]}]}]
            with setup_pg(data, name) as conn:
                assert conn.execute("SELECT version_num FROM public.alembic_version").fetchone() == (revision,)
            bundle = tmp_path / "baseline-input.json"
            bundle.write_text(json.dumps({
                "collector_url": urls["collector"], "query_url": urls["query"],
                "trust": trust, "authority": authority_json, "catalog": json.dumps(catalog),
                "collect": helpers["grant_token"](grant_key, subject), "report": report, "browser": browser,
                "from": (start - timedelta(seconds=1)).isoformat(), "to": (end + timedelta(seconds=1)).isoformat(),
            }), encoding="utf-8")
            old_cwd = old_root / "services/analytics"
            completed = subprocess.run([sys.executable, "-B", "-c", BASELINE_PROCESS, str(bundle)],
                                       cwd=old_cwd, env={**os.environ, "PYTHONPATH": str(old_cwd)},
                                       capture_output=True, text=True, timeout=20)
            assert completed.returncode == 0, completed.stderr[-1600:]
            assert "v2=200 accepted; v3=404; query=200 active_ms=60000" in completed.stdout
            with setup_pg(data, name) as conn:
                count = conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (subject,)).fetchone()
                assert count == (1,)
            print(f"605.C exact old Collector and Query {base[:8]} on {revision}: {completed.stdout.strip()}")
    finally:
        cleanup(data, name, sentinel, revision=revision)
        with setup_pg(data) as conn:
            assert conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        print(f"605.C old-version target={name} owner/sentinel cleanup=confirmed")


BASELINE_PROCESS = """
import json
from pathlib import Path
import sys
import time
from fastapi.testclient import TestClient
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app

fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
common = dict(trust_manifest_json=fixture["trust"], privacy_authority_json=fixture["authority"])
collector = create_app("collector", Settings(database_url=fixture["collector_url"], fixture_catalog_json=fixture["catalog"], **common))
query = create_app("query", Settings(database_url=fixture["query_url"], **common))

def wait(client):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        if client.get("/ready").status_code == 200:
            return
        time.sleep(.05)
    raise AssertionError("legacy readiness deadline")

with TestClient(collector) as client:
    wait(client)
    headers = {"Authorization": "Bearer " + fixture["collect"]}
    response = client.post("/api/analytics/events/batch", headers=headers, json={"events": [fixture["browser"]]})
    assert response.status_code == 200 and response.json()["items"][0]["status"] == "accepted"
    assert client.post("/api/analytics/windows/batch", headers=headers, json={"protocol_version": 1, "windows": []}).status_code == 404

with TestClient(query) as client:
    wait(client)
    result = client.get("/api/analytics/reports/activity", headers={"Authorization": "Bearer " + fixture["report"]},
                        params={"course_id": "backend", "from_time": fixture["from"], "to_time": fixture["to"]})
    assert result.status_code == 200, result.status_code
    totals = result.json()["totals"]
    assert totals["active_ms"] == 60000, totals["active_ms"]
print("v2=200 accepted; v3=404; query=200 active_ms=60000")
"""
