"""Own an opt-in, disposable local Core/Analytics HTTP rehearsal.

Errors retain databases and the private operation record for reconciliation;
no failed or timed-out mutation is retried or silently cleaned up.
"""
import base64
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT.parent / "core-api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cross_service import resources as r


def base_env():
    return {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}}


def core_admin(core_python):
    # The password never enters argv or stdout of this process.
    script = ("import yaml,json,pathlib; "
              "x=yaml.safe_load(pathlib.Path('.local/role-admin/compose.yaml').read_text())['services']['postgres']['environment']; "
              "print(json.dumps({k:x[k] for k in ('POSTGRES_USER','POSTGRES_PASSWORD','POSTGRES_DB')}))")
    p = subprocess.run([str(core_python), "-B", "-c", script], cwd=CORE, env=base_env(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
    values = json.loads(p.stdout)
    if (values["POSTGRES_USER"], values["POSTGRES_DB"]) != ("pymentor_test", "pymentor_test"):
        raise ValueError("Core bootstrap identity mismatch")
    return "postgresql://pymentor_test:" + quote(str(values["POSTGRES_PASSWORD"]), safe="") + "@127.0.0.1:57382/pymentor_test"


def bytes_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def key_material(folder):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    result = {}
    for name in ("grant", "producer", "command", "authority"):
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        bytes_new(folder / (name + ".private.pem"), private)
        bytes_new(folder / (name + ".public.pem"), public)
        result[name] = public.decode("ascii")
    secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=")
    bytes_new(folder / "reader.secret", secret)
    return result, secret.decode("ascii")


def make_fixture(core_db, analytics_db, *, preserve_browser_student=False):
    users = {name: {"id": index, "email": name+"@example.test", "password": secrets.token_urlsafe(24), "subject_ref": str(uuid4())}
             for index, name in enumerate(("admin", "leader", "student", "other"), 1)}
    browser = {name: {"id": index, "email": "browser-" + name + "@example.test", "password": secrets.token_urlsafe(24), "subject_ref": str(uuid4())}
               for name, index in (("admin", 5), ("student", 7))}
    if preserve_browser_student:
        # Disposable loopback-only review identities, never retained users.
        review_password = "Analytics-Local-Review-20261117!"
        users["admin"]["password"] = users["leader"]["password"] = browser["student"]["password"] = review_password
    browser["leader"] = dict(users["leader"])
    return {"core_url": "http://127.0.0.1:18151", "collector_url": "http://127.0.0.1:18152",
            "query_url": "http://127.0.0.1:18153", "privacy_url": "http://127.0.0.1:18154",
            "databases": {"core": core_db, "analytics": analytics_db}, "users": users, "browser_users": browser,
            "course_id": "backend", "catalog_version": "catalog-1", "roster": [users["student"]["subject_ref"], browser["student"]["subject_ref"]] +
                ([browser["admin"]["subject_ref"]] if preserve_browser_student else [])}


def verify_browser_roles(fixture):
    import httpx
    import psycopg
    from cross_service.chain_checks import login
    for user, expected in ((fixture["users"]["admin"], "admin"), (fixture["users"]["leader"], "leader"),
                           (fixture["browser_users"]["student"], "user")):
        with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as client:
            login(client, user)
            response = client.get("/api/user/me")
            if response.status_code != 200 or response.json()["user"]["id"] != user["id"] or response.json()["user"]["role"] != expected:
                raise AssertionError("synthetic browser role verification failed")
            if user["id"] == 7:
                material = client.get("/api/learning/lessons/lesson-1", params={"course_id": "backend"})
                if material.status_code != 200:
                    raise AssertionError("synthetic browser practice material unavailable")
                tasks = material.json()["lesson"]["tasks"]
                if len(tasks) != 1 or tasks[0]["id"] != "lesson-1-task-01" or not tasks[0]["starter_code"]:
                    raise AssertionError("synthetic browser practice task unavailable")
    with psycopg.connect(fixture["databases"]["core"]["dsn"], connect_timeout=3) as conn:
        if conn.execute("SELECT count(*) FROM user_activity_days WHERE user_id=7").fetchone()[0] != 0:
            raise AssertionError("browser student is not fresh")
    print("PASS browser roles, practice material and fresh user via actual Core HTTP/DB", flush=True)


def migrate_core(python, target, folder, *, full_chain=False):
    env = base_env()
    env.update(KNOWI_MIGRATION_URL=target["dsn"].replace("postgresql://", "postgresql+asyncpg://", 1),
               KNOWI_MIGRATION_HOST="127.0.0.1", KNOWI_MIGRATION_DATABASE=target["name"], KNOWI_MIGRATION_USER="pymentor_test",
               PYTHONDONTWRITEBYTECODE="1", CROSS_SERVICE_PRIVATE_DIR=str(folder))
    script = ("from alembic import command; from alembic.config import Config; import sys; "
              "c=Config(sys.argv[1]); c.set_main_option('core_runtime_migrations','true'); command.upgrade(c,'c528_course_leaders')")
    r.run_child([str(python), "-B", "-c", script, str(CORE / "alembic.ini")], cwd=CORE, env=env, timeout=120)
    import psycopg
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        identity = conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        marker = conn.execute("SELECT token FROM public.cross_service_sentinel").fetchall()
        if identity != (target["name"], target["owner"]) or marker != [(target["sentinel"],)]:
            raise ValueError("Core migration target guard failed")
    state = json.loads((folder / "owner.json").read_text(encoding="utf-8"))
    install_env = dict(env, DATABASE_URL=target["dsn"].replace("postgresql://", "postgresql+psycopg://", 1),
                       PYMENTOR_ANALYTICS_INSTALL_SENTINEL=state["core_install_sentinel"])
    r.run_child([str(python), "-B", str(CORE / "tools/analytics_transfer/migrate.py"),
                 "--expected-db", target["name"], "--expected-owner", target["owner"],
                 "--target-version", "2" if full_chain else "1"], cwd=CORE, env=install_env, timeout=120)


def export_and_relay(python, core_env, target, analytics_db, fixture, folder, authority_url, children, job, on_spawn):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import httpx
    import psycopg
    transfer = CORE / "tools/analytics_transfer/run.py"
    task_enabled = core_env.get("PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED") == "1"
    streams = ("activity", "progress", "tasks") if task_enabled else ("activity", "progress")
    for stream in streams:
        for operation in ("export-init", "export-page"):
            r.run_child([str(python), "-B", str(transfer), operation, "--expected-db", target["name"],
                         "--stream", stream, "--limit", "100"], cwd=CORE, env=core_env, timeout=20)
    proxy_port = r.unused_port()
    proxy_env = dict(base_env(), CROSS_SERVICE_PROXY_PORT=str(proxy_port), PYTHONDONTWRITEBYTECODE="1")
    proxy = r.spawn_owned([sys.executable, "-B", str(ROOT / "tests/cross_service/loss_proxy.py")],
                          cwd=ROOT, env=proxy_env, folder=folder, label="lost-reply-proxy", children=children, job=job, on_spawn=on_spawn)
    r.wait_port(proxy, proxy_port)
    job.assert_listener(proxy_port)
    relay_env = dict(core_env, PYMENTOR_ANALYTICS_PRODUCER_ISSUER="control-issuer", PYMENTOR_ANALYTICS_PRODUCER_KID="p",
                     PYMENTOR_ANALYTICS_PRODUCER_ID="synthetic", PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE=str(folder / "producer.private.pem"),
                     PYMENTOR_ANALYTICS_COMMAND_KEY_FILE=str(folder / "command.private.pem"),
                     PYMENTOR_ANALYTICS_GRANT_PUBLIC_KEY_FILE=str(folder / "grant.public.pem"),
                     PYMENTOR_ANALYTICS_AUTHORITY_PUBLIC_KEY_FILE=str(folder / "authority.public.pem"),
                     PYMENTOR_ANALYTICS_AUTHORITY_URL=authority_url, PYMENTOR_ANALYTICS_AUTHORITY_ID="synthetic-authority",
                     PYMENTOR_ANALYTICS_AUTHORITY_ISSUER="authority-issuer", PYMENTOR_ANALYTICS_AUTHORITY_KID="a",
                     PYMENTOR_ANALYTICS_TRANSFER_URL=f"http://127.0.0.1:{proxy_port}")
    cmd = [str(python), "-B", str(transfer), "relay-once", "--expected-db", target["name"]]
    # Stage revision 0 first, then race delivery of that older snapshot against
    # an actual Core HTTP write which allocates a positive live revision.
    with httpx.Client(base_url=fixture["core_url"], timeout=4, trust_env=False) as client:
        student = fixture["users"]["student"]
        login = client.post("/api/user/login", json={"email": student["email"], "password": student["password"]},
                            headers={"Idempotency-Key": str(uuid4())})
        if login.status_code != 200:
            raise RuntimeError("Core synthetic login failed")
        barrier = threading.Barrier(2, timeout=5)
        def relay_initial():
            barrier.wait()
            r.run_child(cmd, cwd=CORE, env=relay_env, timeout=20)
        def write_live():
            barrier.wait()
            result = client.post("/api/user/activity/visit", json={"activity_date": "2026-11-15"})
            if result.status_code != 200:
                raise RuntimeError("Core live activity write failed")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(relay_initial), pool.submit(write_live)
            first.result(timeout=25); second.result(timeout=25)
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        counts = conn.execute("SELECT count(*) FILTER (WHERE status='pending' AND uncertain), count(*) FROM analytics_transfer_outbox").fetchone()
    additional = int(task_enabled) + 2 * (core_env.get("CROSS_SERVICE_LEARNER_COURSE_REVIEW") == "1")
    if counts != (1, 8 + additional):
        raise RuntimeError("lost response did not create exactly one unknown outcome")
    until = time.monotonic() + 20
    while time.monotonic() < until:
        # The Core worker reconciles an uncertain receipt before retrying it.
        r.run_child(cmd, cwd=CORE, env=relay_env, timeout=20)
        with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
            pending = conn.execute("SELECT count(*) FROM analytics_transfer_outbox WHERE status <> 'delivered'").fetchone()[0]
        if not pending:
            break
        time.sleep(.25)
    else:
        raise TimeoutError("Core transfer did not reconcile within deadline")
    until = time.monotonic() + 15
    while time.monotonic() < until:
        with psycopg.connect(analytics_db["dsn"], connect_timeout=3) as conn:
            fact_count = conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE source_kind='core_transfer_v1'").fetchone()[0]
            job_count = conn.execute("SELECT count(*) FROM analytics.processing_jobs WHERE consumer='core.transfer.projection'").fetchone()[0]
            projection_count = conn.execute("SELECT count(*) FROM analytics.core_transfer_projection").fetchone()[0]
        if (fact_count, job_count, projection_count) == (8 + additional, 8 + additional, 7 + additional):
            with psycopg.connect(analytics_db["dsn"], connect_timeout=3) as conn:
                revisions = conn.execute("""SELECT e.revision,f.accepted_event->'payload'->>'visited_at'
                    FROM analytics.core_transfer_entities e JOIN analytics.activity_facts f USING(producer_namespace,event_id,subject_ref)
                    WHERE e.subject_ref=%s AND f.accepted_event->'payload'->>'activity_date'='2026-11-15' ORDER BY e.revision""",
                    (fixture["users"]["student"]["subject_ref"],)).fetchall()
                current = conn.execute("""SELECT p.revision FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f
                    USING(producer_namespace,event_id,subject_ref) WHERE p.subject_ref=%s
                    AND f.accepted_event->'payload'->>'activity_date'='2026-11-15'""",
                    (fixture["users"]["student"]["subject_ref"],)).fetchone()
            if len(revisions) != 2 or revisions[0] != (0, None) or revisions[1][0] < 1 or revisions[1][1] is None or current != (revisions[1][0],):
                raise AssertionError("concurrent backfill/live revision convergence failed")
            return relay_env, proxy
        time.sleep(.1)
    raise AssertionError("Core fact, job or projection invariant failed")


def restore_after_deletion(restored_db, restored_urls, common, roster, subject, folder, children, job, on_spawn, *, full_chain=False, activity_projection=False, day_projection=False, old_run_id=None, new_run_id=None, surviving_day=None):
    import httpx
    import jwt
    import psycopg
    import sqlite3
    from contextlib import closing
    from psycopg import sql
    if day_projection and not (full_chain and activity_projection):
        raise ValueError("day restore requires the full bucket chain")
    if day_projection and (not isinstance(surviving_day, tuple) or len(surviving_day) != 2):
        raise ValueError("day restore requires surviving full-day fixture")
    r.local_ports_free((18156, 18157))
    if activity_projection:
        if old_run_id is None or new_run_id is None or old_run_id == new_run_id:
            raise ValueError("restore requires a fresh external run identity")
        with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
            conn.execute("SELECT analytics.begin_activity_projection_backfill(%s,%s)", (old_run_id, new_run_id))
    query_env = dict(common, ANALYTICS_QUERY_DATABASE_URL=restored_urls["query"], ANALYTICS_QUERY_CORE_TRANSFER_ENABLED="true",
                     ANALYTICS_QUERY_CORE_TRANSFER_NAMESPACE="core", ANALYTICS_QUERY_CORE_TRANSFER_ROSTER_JSON=roster)
    if full_chain:
        query_env["ANALYTICS_QUERY_CORE_TRANSFER_PROOF_ENABLED"] = "true"
    if activity_projection:
        query_env.update(ANALYTICS_QUERY_ACTIVITY_PROJECTION_ENABLED="true", ANALYTICS_ACTIVITY_PROJECTION_RUN_ID=str(new_run_id))
    if day_projection:
        query_env["ANALYTICS_QUERY_ACTIVITY_DAY_QUERY_ENABLED"] = "true"
    query = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "query", "--port", "18156"],
                          cwd=ROOT, env=query_env, folder=folder, label="restored-query", children=children, job=job, on_spawn=on_spawn)
    r.wait_port(query, 18156)
    job.assert_listener(18156)
    with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
        table = "activity_projection_buckets" if activity_projection else "core_transfer_projection"
        assert conn.execute(sql.SQL("SELECT count(*) FROM analytics.{} WHERE subject_ref=%s").format(sql.Identifier(table)), (subject,)).fetchone()[0] > 0
    now = int(time.time())
    token = jwt.encode({"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": subject,
                        "iat": now, "nbf": now, "exp": now+60, "role": "user", "scope": "report:read",
                        "course_ids": ["backend"], "subject_refs": [], "global_read": False},
                       (folder / "grant.private.pem").read_bytes(), algorithm="EdDSA", headers={"kid": "g"})
    with httpx.Client(base_url="http://127.0.0.1:18156", timeout=4, trust_env=False) as http:
        # Prove that this is an old prefix, rather than an unrelated outage.
        with closing(sqlite3.connect("file:" + (folder / "journal.sqlite").as_posix() + "?mode=ro", uri=True, timeout=3)) as journal:
            journal_tip = journal.execute("SELECT high_watermark FROM authority_meta WHERE singleton=1").fetchone()[0]
        with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
            assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone()[0] < journal_tip
        # There is no restored privacy worker yet, so the old prefix must not
        # admit reads or processing.
        time.sleep(1)
        assert query.poll() is None and http.get("/ready").status_code == 503
        blocked = http.get("/internal/analytics/v1/legacy/calendar", params={"year": 2026, "month": 11},
                           headers={"Authorization": "Bearer " + token})
        assert blocked.status_code == 503
        # A worker using the *new* generation must also stay closed while the
        # restored privacy prefix trails the external journal.
        if activity_projection:
            def work_state():
                with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
                    tables = ("activity_projection_backfill", "activity_projection_buckets",
                              "activity_projection_day_backfill", "activity_projection_day_sources",
                              "activity_projection_days", "processing_jobs") if day_projection else (
                                  "activity_projection_backfill", "activity_projection_buckets", "processing_jobs")
                    return tuple(conn.execute(sql.SQL("SELECT COALESCE(array_agg(row_to_json(t)::text ORDER BY row_to_json(t)::text), ARRAY[]::text[]) FROM analytics.{} t").format(sql.Identifier(table))).fetchone()[0] for table in tables)
            before_barrier = work_state()
            blocked_env = dict(common, ANALYTICS_PROCESSOR_DATABASE_URL=restored_urls["processor"],
                               ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED="true", ANALYTICS_ACTIVITY_PROJECTION_RUN_ID=str(new_run_id))
            if day_projection:
                blocked_env["ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED"] = "true"
            blocked_worker = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18159"],
                                           cwd=ROOT, env=blocked_env, folder=folder, label="restored-blocked-worker", children=children, job=job, on_spawn=on_spawn)
            r.wait_port(blocked_worker, 18159); job.assert_listener(18159)
            time.sleep(1.2)
            assert blocked_worker.poll() is None
            assert httpx.get("http://127.0.0.1:18159/ready", timeout=4, trust_env=False).status_code == 503
            assert work_state() == before_barrier
            r.processes_stop([blocked_worker])
        privacy_env = dict(common, ANALYTICS_PRIVACY_DATABASE_URL=restored_urls["privacy"])
        privacy = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "privacy", "--port", "18157"],
                                cwd=ROOT, env=privacy_env, folder=folder, label="restored-privacy", children=children, job=job, on_spawn=on_spawn)
        r.wait_http(privacy, "http://127.0.0.1:18157/ready")
        if activity_projection:
            assert http.get("/ready").status_code == 503
            with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
                before = conn.execute("SELECT run_id,phase,cursor_namespace,cursor_event_id FROM analytics.activity_projection_backfill").fetchone()
                buckets_before = conn.execute("SELECT subject_ref,bucket_start,state,generation,projection_generation FROM analytics.activity_projection_buckets ORDER BY subject_ref,bucket_start").fetchall()
                jobs_before = conn.execute("SELECT producer_namespace,event_id,consumer,status FROM analytics.processing_jobs ORDER BY producer_namespace,event_id,consumer").fetchall()
                if day_projection:
                    days_before = conn.execute("SELECT subject_ref,local_day,state,generation FROM analytics.activity_projection_day_sources ORDER BY subject_ref,local_day").fetchall()
                    day_backfill_before = conn.execute("SELECT run_id,phase,cursor_subject,cursor_bucket,cursor_metric FROM analytics.activity_projection_day_backfill").fetchall()
                    assert days_before and day_backfill_before == [(None, "scanning", None, None, None)]
            assert before == (new_run_id, "scanning", None, None) and buckets_before
            old_env = dict(common, ANALYTICS_PROCESSOR_DATABASE_URL=restored_urls["processor"],
                           ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED="true", ANALYTICS_ACTIVITY_PROJECTION_RUN_ID=str(old_run_id))
            if day_projection:
                old_env["ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED"] = "true"
            old_worker = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18159"],
                                       cwd=ROOT, env=old_env, folder=folder, label="restored-old-worker", children=children, job=job, on_spawn=on_spawn)
            r.wait_port(old_worker, 18159); job.assert_listener(18159)
            time.sleep(1.2)
            with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
                after = conn.execute("SELECT run_id,phase,cursor_namespace,cursor_event_id FROM analytics.activity_projection_backfill").fetchone()
                buckets_after = conn.execute("SELECT subject_ref,bucket_start,state,generation,projection_generation FROM analytics.activity_projection_buckets ORDER BY subject_ref,bucket_start").fetchall()
                jobs_after = conn.execute("SELECT producer_namespace,event_id,consumer,status FROM analytics.processing_jobs ORDER BY producer_namespace,event_id,consumer").fetchall()
                if day_projection:
                    assert days_before == conn.execute("SELECT subject_ref,local_day,state,generation FROM analytics.activity_projection_day_sources ORDER BY subject_ref,local_day").fetchall()
                    assert day_backfill_before == conn.execute("SELECT run_id,phase,cursor_subject,cursor_bucket,cursor_metric FROM analytics.activity_projection_day_backfill").fetchall()
                    assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days").fetchone()[0] == 0
            assert before == after and buckets_before == buckets_after and jobs_before == jobs_after and http.get("/ready").status_code == 503
            r.processes_stop([old_worker])
            new_env = dict(common, ANALYTICS_PROCESSOR_DATABASE_URL=restored_urls["processor"],
                           ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED="true", ANALYTICS_ACTIVITY_PROJECTION_RUN_ID=str(new_run_id))
            if day_projection:
                new_env["ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED"] = "true"
            new_worker = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18159"],
                                       cwd=ROOT, env=new_env, folder=folder, label="restored-new-worker", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(new_worker, "http://127.0.0.1:18159/ready"); job.assert_listener(18159)
            if day_projection:
                from cross_service.a011_checks import wait_day_ready
                wait_day_ready(restored_db, new_run_id)
        r.wait_http(query, "http://127.0.0.1:18156/ready")
        job.assert_listener(18157)
        if day_projection:
            from cross_service.a011_checks import assert_restored_day
            assert_restored_day("http://127.0.0.1:18156", restored_db, folder, new_run_id, *surviving_day)
        if day_projection:
            now = int(time.time())
            token = jwt.encode({"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": subject,
                                "iat": now, "nbf": now, "exp": now+60, "role": "user", "scope": "report:read",
                                "course_ids": ["backend"], "subject_refs": [], "global_read": False},
                               (folder / "grant.private.pem").read_bytes(), algorithm="EdDSA", headers={"kid": "g"})
        denied = http.get("/internal/analytics/v1/legacy/calendar", params={"year": 2026, "month": 11},
                          headers={"Authorization": "Bearer " + token})
        assert denied.status_code == 403
    with psycopg.connect(restored_db["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s", (subject,)).fetchone() == (True,)
        with closing(sqlite3.connect("file:" + (folder / "journal.sqlite").as_posix() + "?mode=ro", uri=True, timeout=3)) as journal:
            current = journal.execute("SELECT high_watermark FROM authority_meta WHERE singleton=1").fetchone()[0]
        assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone()[0] == current
        if activity_projection:
            assert conn.execute("SELECT run_id,phase FROM analytics.activity_projection_backfill").fetchone() == (new_run_id, "complete")
            assert conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state<>'ready'").fetchone()[0] == 0
        if day_projection:
            from cross_service.a011_checks import assert_no_old_bucket_or_day_claims
            assert conn.execute("SELECT run_id,phase FROM analytics.activity_projection_day_backfill").fetchone() == (new_run_id, "complete")
            assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days WHERE run_id<>%s", (new_run_id,)).fetchone()[0] == 0
            assert_no_old_bucket_or_day_claims(conn, old_run_id)
            assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days").fetchone()[0] > 0
        tables = ("activity_facts", "processing_jobs", "processing_receipts", "core_transfer_entities", "core_transfer_projection")
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        sidecars = ("activity_course_buckets", "activity_course_days", "activity_credit_windows", "activity_credit_days") if revision == "a014" else ()
        for table in tables + (("core_snapshot_proofs",) if full_chain else ()) + (("activity_projection_buckets",) if activity_projection else ()) + (("activity_projection_day_sources", "activity_projection_days") if day_projection else ()) + sidecars:
            assert conn.execute(sql.SQL("SELECT count(*) FROM analytics.{} WHERE subject_ref=%s").format(sql.Identifier(table)), (subject,)).fetchone()[0] == 0
    r.processes_stop([query, privacy] + ([new_worker] if activity_projection else []))


def release_hold(operation):
    if not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise ValueError("invalid operation ID")
    root = (ROOT / ".local/cross-service").resolve(strict=True)
    folder = (root / operation).resolve(strict=True)
    if folder.parent != root:
        raise ValueError("invalid operation directory")
    owner_file, token_file = folder / "owner.json", folder / "hold.token"
    if (owner_file.is_symlink() or token_file.is_symlink() or owner_file.resolve(strict=True).parent != folder
            or token_file.resolve(strict=True).parent != folder):
        raise ValueError("invalid private release files")
    state = json.loads(owner_file.read_text(encoding="utf-8"))
    if state.get("state") != "browser_hold":
        raise ValueError("operation is not holding browser services")
    secret = token_file.read_bytes()
    if len(secret) != 64:
        raise ValueError("invalid hold token")
    bytes_new(folder / "release.request", secret)
    print("Release requested for synthetic operation", operation)


def browser_hold(folder, owner_token, seconds, processes):
    secret = secrets.token_hex(32).encode("ascii")
    bytes_new(folder / "hold.token", secret)
    deadline = time.monotonic() + seconds
    r.record(folder, owner_token, state="browser_hold", browser_holder_pid=os.getpid(), hold_expires_unix=int(time.time()) + seconds)
    print(f"HOLD synthetic operation {folder.name}; expires in {seconds}s; release with --release {folder.name}", flush=True)
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("an owned browser service exited during hold")
        request = folder / "release.request"
        if request.exists():
            if (request.is_symlink() or request.resolve(strict=True).parent != folder or request.stat().st_size != len(secret)
                    or not hmac.compare_digest(request.read_bytes(), secret)):
                raise ValueError("invalid release request")
            print("Synthetic browser hold released", flush=True)
            return
        time.sleep(.25)
    print("Synthetic browser hold expired", flush=True)


def reconcile_outbox(python, env, target):
    import psycopg
    transfer = CORE / "tools/analytics_transfer/run.py"
    cmd = [str(python), "-B", str(transfer), "relay-once", "--expected-db", target["name"]]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
            states = conn.execute("SELECT status,count(*) FROM analytics_transfer_outbox GROUP BY status").fetchall()
        counts = dict(states)
        if counts.get("quarantined", 0):
            raise RuntimeError("quarantined synthetic outbox requires reconciliation")
        if not counts.get("pending", 0) and not counts.get("claimed", 0):
            return
        r.run_child(cmd, cwd=CORE, env=env, timeout=20)
        time.sleep(.25)
    raise TimeoutError("synthetic outbox remains unresolved after browser hold")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true")
    group.add_argument("--release", metavar="OPERATION_UUID")
    parser.add_argument("--hold-seconds", type=int, default=0)
    parser.add_argument("--browser-pages", action="store_true", help="populate report pagination during a full browser hold")
    parser.add_argument("--chain", action="store_true", help="fresh a007-a009 proof/catalog acceptance")
    parser.add_argument("--a010", action="store_true", help="include the synthetic a010 activity projection")
    parser.add_argument("--a011", action="store_true", help="include the synthetic a011 day projection")
    parser.add_argument("--a012", action="store_true", help="rehearse course projections and keep collection running during the browser hold")
    parser.add_argument("--a014", action="store_true", help="same-schema a014 switch, rollback and logical restore privacy")
    parser.add_argument("--rollback-read", action="store_true", help="restart Core on its legacy reader after replay and return to Analytics")
    parser.add_argument("--publisher", action="store_true", help="exercise the autonomous Core publisher on a fresh no-hold chain")
    parser.add_argument("--progress-history", action="store_true", help="verify task progress and date-only history on the synthetic chain")
    parser.add_argument("--admin-completions", action="store_true", help="check the global completion read switch on the synthetic chain")
    parser.add_argument("--learner-course-review", action="store_true", help="verify and hold the learner and course read switches for browser review")
    parser.add_argument("--publisher-image", metavar="SHA256", help="run the publisher in an exact local Linux image")
    args = parser.parse_args()
    if args.release:
        if args.hold_seconds or args.chain or args.a010 or args.a011 or args.a012 or args.a014 or args.rollback_read or args.publisher or args.publisher_image or args.browser_pages or args.progress_history or args.admin_completions or args.learner_course_review:
            parser.error("hold duration applies only to --run")
        release_hold(args.release)
        return 0
    if args.hold_seconds and not 60 <= args.hold_seconds <= 3600:
        parser.error("hold duration must be 60 to 3600 seconds")
    if args.a010 and not args.chain:
        parser.error("a010 requires the full preceding chain")
    if args.a011 and not (args.chain and args.a010):
        parser.error("a011 requires --chain and --a010")
    if args.rollback_read and not (args.chain and args.a010 and args.a011 and not args.hold_seconds):
        parser.error("rollback-read requires --chain --a010 --a011 without browser hold")
    if args.a014 and not (args.rollback_read and not args.a012 and not args.publisher and not args.publisher_image):
        parser.error("a014 requires --rollback-read without a012 or publisher options")
    if args.a012 and not (args.a011 and args.learner_course_review):
        parser.error("a012 rehearsal requires --a011 and --learner-course-review")
    if args.progress_history and (not args.chain or (args.hold_seconds and not args.learner_course_review) or args.publisher_image):
        parser.error("progress-history requires a source chain and dedicated learner-course browser hold")
    if args.admin_completions and not args.progress_history:
        parser.error("admin-completions requires progress-history")
    if args.learner_course_review and not (args.admin_completions and args.a011 and args.hold_seconds and not args.browser_pages):
        parser.error("learner-course-review requires source completion reads, a011 and a bounded browser hold")
    if args.publisher and (not args.chain or not args.a010 or args.hold_seconds):
        parser.error("publisher requires a010 full chain without browser hold")
    if args.publisher_image and (not args.publisher or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.publisher_image)):
        parser.error("publisher-image requires --publisher and an exact sha256 image ID")
    if args.browser_pages and (not args.chain or not args.a010 or args.hold_seconds != 3600 or args.publisher or args.publisher_image):
        parser.error("browser-pages requires full chain and a 3600-second browser hold without publisher")
    if os.environ.get("ANALYTICS_CROSS_SERVICE_GO") != "1":
        print("Requires root runtime GO and --run; no resources opened.", file=sys.stderr)
        return 2
    core_python = CORE / ".venv/Scripts/python.exe"
    if not core_python.is_file() or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Core 3.14 and Analytics 3.12 environments required")
    if r.available_memory_mib() - 350 < 1536:
        raise RuntimeError("insufficient measured memory headroom for Vite reserve")
    r.local_ports_free((18151, 18152, 18153, 18154, 18155, 18156, 18157, 18158, 18159))
    folder, token = r.private_folder()
    children = []
    job = None
    core_db = analytics_db = analytics_data = restored_db = restored_data = None
    successful = False
    monitor_stop, memory_short = threading.Event(), threading.Event()
    monitor = None
    publisher_container = None
    redis_prefix = "cross-service-" + uuid4().hex
    try:
        # Persist ownership before any child can create Redis keys, including
        # when memory pressure interrupts the run before HTTP acceptance.
        r.record(folder, token, redis_prefix=redis_prefix)
        job = r.OwnedJob()
        r.set_job(job)
        def watch_memory():
            while not monitor_stop.wait(.5):
                try:
                    safe = r.available_memory_mib() >= 1536
                except Exception:
                    safe = False
                if not safe:
                    memory_short.set()
                    job.close()  # Only processes in this run's owned job.
                    return
        monitor = threading.Thread(target=watch_memory, daemon=True)
        monitor.start()
        def on_spawn(processes):
            r.record(folder, token, state="processes_starting", processes=[p.pid for p in processes])
        admin = core_admin(core_python)
        core_db = r.create_core(admin, folder, token)
        analytics_db, urls, analytics_data = r.create_analytics(folder, token)
        restored_db, restored_urls, restored_data = r.create_analytics(folder, token)
        preserve_browser_student = bool(args.chain and args.hold_seconds and (args.a012 or not args.learner_course_review))
        fixture = make_fixture(core_db, analytics_db, preserve_browser_student=preserve_browser_student)
        r.write_new(folder / "fixture.json", fixture)
        core_env = base_env()
        core_env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(CORE), CROSS_SERVICE_PRIVATE_DIR=str(folder),
                        DATABASE_URL=core_db["dsn"].replace("postgresql://", "postgresql+psycopg://", 1),
                        REDIS_URL="redis://127.0.0.1:57381/13", KNOWI_REDIS_PREFIX=redis_prefix,
                        KNOWI_LOGIN_RECOVERY_KEY=secrets.token_hex(32), PYMENTOR_PROFILE="postgres_redis", PYMENTOR_SCHEMA_MODE="managed",
                        PYMENTOR_ANALYTICS_DELIVERY_ENABLED="1", PYMENTOR_ANALYTICS_GRANTS_ENABLED="1",
                        PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED="0",
                        PYMENTOR_ANALYTICS_GRANT_ISSUER="grant-issuer", PYMENTOR_ANALYTICS_GRANT_KID="g",
                        PYMENTOR_ANALYTICS_GRANT_KEY_FILE=str(folder / "grant.private.pem"), PYMENTOR_COURSES_ROOT=str(folder / "courses"))
        if args.hold_seconds:
            core_env["CROSS_SERVICE_BROWSER_PRACTICE"] = "1"
        if args.learner_course_review:
            core_env["CROSS_SERVICE_LEARNER_COURSE_REVIEW"] = "1"
            core_env["CROSS_SERVICE_BROWSER_EXTRA"] = "1"
        if args.progress_history:
            core_env["PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED"] = "1"
            core_env["PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY"] = "1"
        if preserve_browser_student and args.a010:
            core_env["CROSS_SERVICE_BROWSER_EXTRA"] = "1"
        if args.browser_pages:
            core_env["CROSS_SERVICE_BROWSER_PAGES"] = "1"
        if preserve_browser_student:
            core_env["CROSS_SERVICE_DELETE_SEPARATE_STUDENT"] = "1"
        if args.chain:
            core_env.update(PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED="1", PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED="1",
                            PYMENTOR_ANALYTICS_CATALOG_FIXTURE_DIR=str(folder),
                            PYTHONPATH=str(CORE) + os.pathsep + str(ROOT / "tests/cross_service"))
        core_fixture = ROOT / "tests/cross_service/core_fixture.py"
        r.run_child([str(core_python), "-B", str(core_fixture), "courses"], cwd=CORE, env=core_env)
        migrate_core(core_python, core_db, folder, full_chain=args.chain)
        r.install_analytics(urls, full_chain=args.chain, activity_projection=args.a010, day_projection=args.a011)
        r.install_analytics(restored_urls, full_chain=args.chain, activity_projection=args.a010, day_projection=args.a011)
        if args.a012 or args.a014:
            from pymentor_analytics.adapters.postgres.migrate import upgrade
            from pymentor_analytics.app.config import Settings
            for target in (urls, restored_urls):
                upgrade(Settings(database_url=target["migrate"]), target_revision="a014" if args.a014 else "a012")
        r.verify_analytics_grants(analytics_db)
        r.verify_analytics_grants(restored_db)
        print("PASS synthetic schemas and role grants", flush=True)
        r.run_child([str(core_python), "-B", str(core_fixture), "seed"], cwd=CORE, env=core_env)
        keys, secret = key_material(folder)
        projection_run = uuid4() if args.a010 else None
        if args.a010:
            import psycopg
            with psycopg.connect(analytics_db["dsn"], connect_timeout=3) as conn:
                conn.execute("SELECT analytics.begin_activity_projection_backfill(NULL,%s)", (projection_run,))
        authority_port = r.unused_port()
        authority_url = f"http://127.0.0.1:{authority_port}"
        authority_json = json.dumps({"url": authority_url, "authority_id": "synthetic-authority", "issuer": "authority-issuer",
                                     "public_keys": {"a": keys["authority"]}, "reader_secret": secret, "fixture_mode": True})
        trust = json.dumps({"grant": {"issuer": "grant-issuer", "keys": {"g": keys["grant"]}},
                            "producer": {"issuer": "control-issuer", "keys": {"p": keys["producer"]}, "allowlist": ["synthetic"]}})
        catalog = json.dumps([{"schema_version": 2, "kind": "catalog_snapshot", "control_id": str(uuid4()), "course_id": "backend",
                               "catalog_version": "catalog-1", "track_id": "synthetic-track", "lessons": [{"lesson_id": "lesson-1"}]}])
        authority_config = {"fixture_mode": True, "bind_host": "127.0.0.1", "port": authority_port, "issuer": "authority-issuer", "kid": "a",
                            "signing_key_file": str(folder / "authority.private.pem"), "producer_issuer": "control-issuer",
                            "producer_public_keys": {"p": keys["producer"]}, "allowed_producers": ["synthetic"],
                            "producer_command_public_keys": {"synthetic": keys["command"]}, "reader_secret_file": str(folder / "reader.secret")}
        r.write_new(folder / "authority.json", authority_config)
        authority_tool = ROOT / "tools/synthetic_privacy_authority.py"
        env = base_env(); env.update(PYTHONDONTWRITEBYTECODE="1", CROSS_SERVICE_PRIVATE_DIR=str(folder))
        r.run_child([sys.executable, "-B", str(authority_tool), "init", "--journal", str(folder / "journal.sqlite"),
                     "--authority-id", "synthetic-authority"], cwd=ROOT, env=env)
        authority_process = r.spawn_owned([sys.executable, "-B", str(authority_tool), "serve", "--journal", str(folder / "journal.sqlite"),
                       "--authority-id", "synthetic-authority", "--config", str(folder / "authority.json")],
                      cwd=ROOT, env=env, folder=folder, label="authority", children=children, job=job, on_spawn=on_spawn)
        common = dict(env, ANALYTICS_TRUST_MANIFEST_JSON=trust, ANALYTICS_PRIVACY_AUTHORITY_JSON=authority_json)
        if not args.chain:
            common["ANALYTICS_FIXTURE_CATALOG_JSON"] = catalog
        processes, role_envs = {}, {}
        roster = json.dumps({"complete": True, "expires_at": int(time.time()) + 3600,
                             "subjects": [u["subject_ref"] for u in fixture["users"].values()] +
                                         [fixture["browser_users"][name]["subject_ref"] for name in ("admin", "student")],
                             "students": [fixture[group][name]["subject_ref"] for group, names in
                                          (("users", ("leader", "student", "other")), ("browser_users", ("student",))) for name in names] +
                                         ([fixture["browser_users"]["admin"]["subject_ref"]] if preserve_browser_student else [])})
        collector_port = 18158 if args.chain else 18152
        for role, role_port in (("privacy", 18154), ("collector", collector_port), ("query", 18153), ("processor", 18155)):
            role_env = dict(common, **{f"ANALYTICS_{role.upper()}_DATABASE_URL": urls[role]})
            if args.a010 and role in {"processor", "query"}:
                role_env["ANALYTICS_ACTIVITY_PROJECTION_RUN_ID"] = str(projection_run)
            if role == "collector":
                role_env.update(ANALYTICS_COLLECTOR_CORE_TRANSFER_ENABLED="true",
                                ANALYTICS_COLLECTOR_CORE_TRANSFER_NAMESPACES_JSON=json.dumps({"synthetic": "core"}),
                                ANALYTICS_COLLECTOR_CORE_TRANSFER_STATUS_DATABASE_URL=urls["transfer_status"])
                if args.chain:
                    role_env.update(ANALYTICS_COLLECTOR_CORE_TRANSFER_PROOF_ENABLED="true",
                                    ANALYTICS_COLLECTOR_CATALOG_ENABLED="true", ANALYTICS_COLLECTOR_CATALOG_NAMESPACE="core")
            elif role == "processor":
                role_env["ANALYTICS_PROCESSOR_CORE_TRANSFER_ENABLED"] = "true"
                if args.chain:
                    role_env["ANALYTICS_PROCESSOR_CORE_TRANSFER_PROOF_ENABLED"] = "true"
                if args.a010:
                    role_env["ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED"] = "true"
                if args.a011:
                    role_env["ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED"] = "true"
                if args.a012:
                    role_env["ANALYTICS_PROCESSOR_ACTIVITY_COURSE_WORKER_ENABLED"] = "true"
            elif role == "query":
                role_env.update(ANALYTICS_QUERY_CORE_TRANSFER_ENABLED="true", ANALYTICS_QUERY_CORE_TRANSFER_NAMESPACE="core",
                                ANALYTICS_QUERY_CORE_TRANSFER_ROSTER_JSON=roster)
                if args.a010:
                    role_env["ANALYTICS_QUERY_ACTIVITY_PROJECTION_ENABLED"] = "true"
                if args.a011:
                    role_env["ANALYTICS_QUERY_ACTIVITY_DAY_QUERY_ENABLED"] = "true"
                if args.a012:
                    role_env["ANALYTICS_QUERY_ACTIVITY_COURSE_QUERY_ENABLED"] = "true"
            role_envs[role] = role_env
            if args.a010 and role == "processor":
                continue
            processes[role] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", role, "--port", str(role_port)],
                                             cwd=ROOT, env=role_env, folder=folder, label=role, children=children, job=job, on_spawn=on_spawn)
        entrypoint = "core_instrumented:create_app" if args.chain else "pymentor_core.app.asgi:create_app"
        processes["core"] = r.spawn_owned([str(core_python), "-B", "-m", "uvicorn", entrypoint, "--factory",
                                            "--host", "127.0.0.1", "--port", "18151", "--no-access-log"],
                                           cwd=CORE, env=core_env, folder=folder, label="core", children=children, job=job, on_spawn=on_spawn)
        r.record(folder, token, state="processes_started", processes=[p.pid for p in children])
        if memory_short.is_set() or r.available_memory_mib() < 1536:
            raise RuntimeError("memory headroom below 1536 MiB")
        print("Memory headroom after process start:", r.available_memory_mib(), "MiB", flush=True)
        r.wait_http(processes["core"], fixture["core_url"] + "/health/ready")
        for role, role_port in (("privacy", 18154), ("collector", collector_port), ("query", 18153), ("processor", 18155)):
            if args.a010 and role == "processor":
                continue
            suffix = "/live" if args.a010 and role == "query" else "/ready"
            r.wait_http(processes[role], f"http://127.0.0.1:{role_port}" + suffix)
        catalog_proxy = None
        if args.chain:
            proxy_env = dict(env, CROSS_SERVICE_PROXY_PORT="18152", CROSS_SERVICE_PROXY_UPSTREAM_PORT="18158",
                             CROSS_SERVICE_PROXY_MODE="catalog", CROSS_SERVICE_PROXY_MARKER=str(folder / "catalog-lost-reply.marker"))
            catalog_proxy = r.spawn_owned([sys.executable, "-B", str(ROOT / "tests/cross_service/loss_proxy.py")], cwd=ROOT,
                                          env=proxy_env, folder=folder, label="catalog-proxy", children=children, job=job, on_spawn=on_spawn)
            r.wait_port(catalog_proxy, 18152)
        owned_ports = (authority_port, 18151, 18152, 18153, 18154) + (() if args.a010 else (18155,)) + ((18158,) if args.chain else ())
        for owned_port in owned_ports:
            job.assert_listener(owned_port)
        if args.chain:
            from cross_service.chain_checks import publish_catalog
            r.record(folder, token, state="processes_started", processes=[p.pid for p in children])
            publish_catalog(core_python, core_env, folder, token, fixture, analytics_db, browser_pages=args.browser_pages)
            print("PASS P1 catalog lost-ACK reconciliation and Core metadata access/digest", flush=True)
        if args.a010:
            from cross_service import chain_checks as chain
            historical_subject = chain.seed_historical_browser(fixture, analytics_db, folder)
            processes["processor"] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18155"],
                                                   cwd=ROOT, env=role_envs["processor"], folder=folder, label="processor", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["processor"], "http://127.0.0.1:18155/ready"); job.assert_listener(18155)
            r.wait_http(processes["query"], "http://127.0.0.1:18153/ready")
            chain.assert_projection_backfill(analytics_db, projection_run, historical_subject)
            print("PASS actual a010 historical browser backfill and processor drain", flush=True)
            if args.a011:
                from cross_service import a011_checks
                a011_checks.wait_day_ready(analytics_db, projection_run)
                day_fixture = a011_checks.assert_real_day_flow(fixture, analytics_db, folder, projection_run)
        relay_env, proxy = export_and_relay(core_python, core_env, core_db, analytics_db, fixture, folder, authority_url, children, job, on_spawn)
        print("PASS concurrent Core revisions and lost-reply receipt reconciliation", flush=True)
        from cross_service.restore import snapshot, transplant_fresh
        subject = fixture["users"]["other"]["subject_ref"]
        if not args.chain:
            old_snapshot = snapshot(analytics_db, subject)
            transplant_fresh(restored_db, old_snapshot)
        r.record(folder, token, state="http_acceptance_running", redis_prefix=redis_prefix)
        test_env = dict(relay_env, ANALYTICS_CROSS_SERVICE_RUN="1", ANALYTICS_CROSS_SERVICE_FIXTURE=str(folder / "fixture.json"),
                        CROSS_SERVICE_CORE_PYTHON=str(core_python), CROSS_SERVICE_CORE_ROOT=str(CORE),
                        CROSS_SERVICE_TRANSFER_SCRIPT=str(CORE / "tools/analytics_transfer/run.py"),
                        CROSS_SERVICE_JOURNAL=str(folder / "journal.sqlite"))
        if args.a010:
            test_env["CROSS_SERVICE_A010"] = "1"
        test_result = r.spawn_owned([sys.executable, "-B", "-m", "pytest", "tests/cross_service/test_live_http.py", "-q", "-x",
                                     "--tb=short", "-p", "no:cacheprovider"], cwd=ROOT, env=test_env,
                                    folder=folder, label="http-tests", children=children, job=job, on_spawn=on_spawn)
        if test_result.wait(timeout=150):
            raise AssertionError("HTTP acceptance failed; see private test log")
        print("PASS six HTTP parity, grant, replay, time and deletion scenarios", flush=True)
        if args.chain:
            from cross_service import chain_checks as chain
            test_student = fixture["browser_users"]["admin" if preserve_browser_student else "student"]
            chain.verify_proof_roles(analytics_db)
            chain.verify_proof_roles(restored_db)
            if args.a010:
                from cross_service import a010_checks
                a010_checks.verify_projection_roles(analytics_db)
                a010_checks.verify_projection_roles(restored_db)
            chain.bigint_roundtrip(core_db)
            if not args.a010:
                chain.seed_browser_student_activity(fixture, core_db, analytics_db,
                                                    lambda: reconcile_outbox(core_python, relay_env, core_db), user=test_student)
            if args.progress_history:
                r.run_child([str(core_python), "-B", str(core_fixture), "proof-write-task"], cwd=CORE, env=core_env)
                reconcile_outbox(core_python, relay_env, core_db)
            r.processes_stop([processes["processor"]])
            r.run_child([str(core_python), "-B", str(core_fixture), "proof-stage"], cwd=CORE, env=core_env, timeout=40)
            r.run_child([str(core_python), "-B", str(core_fixture), "proof-accepted"], cwd=CORE, env=relay_env, timeout=40)
            pending_proof = chain.assert_received_pending(core_db, analytics_db, fixture, folder)
            processes["processor"] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18155"],
                                                   cwd=ROOT, env=role_envs["processor"], folder=folder, label="processor-proof", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["processor"], "http://127.0.0.1:18155/ready"); job.assert_listener(18155)
            r.run_child([str(core_python), "-B", str(core_fixture), "proof-verified"], cwd=CORE, env=relay_env, timeout=40)
            chain.assert_received_verified(fixture, folder, pending_proof, analytics_db)
            r.processes_stop([processes["query"]])
            proof_query_env = dict(role_envs["query"], ANALYTICS_QUERY_CORE_TRANSFER_PROOF_ENABLED="true")
            processes["query"] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "query", "--port", "18153"],
                                               cwd=ROOT, env=proof_query_env, folder=folder, label="query-proof", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["query"], "http://127.0.0.1:18153/ready"); job.assert_listener(18153)
            chain.assert_complete_query(fixture, core_db)
            if args.progress_history:
                chain.assert_progress_and_history(fixture, core_db, analytics_db)
                print("PASS Core task revisions and date-only import -> verified proof v2 -> signed history Query", flush=True)
            print("PASS P1 proof pending-to-verified, G1 self cut, BIGINT and role grants", flush=True)
            chain.assert_live_sql_detector(fixture)
            baseline_profile = chain.baseline_admin_profile(fixture)
            baseline_completions = chain.global_completion_views(fixture, switched=False) if args.admin_completions else None
            r.processes_stop([processes["core"]])
            analytics_core_env = dict(core_env, PYMENTOR_ANALYTICS_LEGACY_READ_MODE="analytics",
                                      PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED="1" if args.a010 else "0",
                                      PYMENTOR_ANALYTICS_QUERY_URL=fixture["query_url"])
            if args.admin_completions:
                analytics_core_env["PYMENTOR_ANALYTICS_ADMIN_COMPLETIONS_ENABLED"] = "1"
            if args.learner_course_review:
                analytics_core_env["PYMENTOR_ANALYTICS_LEARNER_READS_ENABLED"] = "1"
            processes["core"] = r.spawn_owned([str(core_python), "-B", "-m", "uvicorn", entrypoint, "--factory",
                                                "--host", "127.0.0.1", "--port", "18151", "--no-access-log"],
                                               cwd=CORE, env=analytics_core_env, folder=folder, label="core-analytics", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["core"], fixture["core_url"] + "/health/ready"); job.assert_listener(18151)
            chain.assert_guarded_core_reads(fixture, browser=args.a010)
            if args.admin_completions:
                assert chain.global_completion_views(fixture, switched=True) == baseline_completions
                print("PASS two global admin dashboards match Core without reading Core lesson progress", flush=True)
            if args.learner_course_review:
                chain.assert_learner_course_reads(fixture)
                print("PASS Core learner history, measured calendar and course completion ACL on live Query", flush=True)
                paused_roles = () if args.a012 else (("processor", 18155), ("privacy", 18154), ("collector", collector_port))
                r.processes_stop([processes[role] for role, _ in paused_roles])
                held_processes = [authority_process, catalog_proxy, proxy]
                held_processes.extend(process for role, process in processes.items()
                                      if role not in {name for name, _ in paused_roles})
                browser_hold(folder, token, args.hold_seconds, held_processes)
                for role, port in paused_roles:
                    processes[role] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", role, "--port", str(port)],
                                                    cwd=ROOT, env=role_envs[role], folder=folder, label=f"{role}-after-browser",
                                                    children=children, job=job, on_spawn=on_spawn)
                    r.wait_http(processes[role], f"http://127.0.0.1:{port}/ready"); job.assert_listener(port)
            if args.a010:
                baseline_profile = chain.mixed_profile_baseline(fixture, core_db, analytics_db, baseline_profile,
                                                               dates_only=args.progress_history)
            print("PASS Core signed self/admin roster and guarded analytics reads", flush=True)

            browser_payload = None
            if args.a010:
                browser_payload = a010_checks.browser_bridge(fixture, core_db, analytics_db, user=test_student)
                a010_checks.wait_ready(analytics_db)
                r.processes_stop([processes["processor"]])
                a010_checks.claim_handoff(fixture, analytics_db, urls, folder, projection_run, authority_json)
                processes["processor"] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18155"],
                                                       cwd=ROOT, env=role_envs["processor"], folder=folder, label="processor-handoff", children=children, job=job, on_spawn=on_spawn)
                r.wait_http(processes["processor"], "http://127.0.0.1:18155/ready"); job.assert_listener(18155)
                print("PASS B2 browser bridge, bucket totals/edges/details and two-worker generation fencing", flush=True)

            wire, extra_subjects, _ = chain.independent_verified_proofs(fixture, analytics_db, folder)
            r.processes_stop([processes["processor"]])
            chain.assert_late_invalidation(fixture, analytics_db, folder, wire, extra_subjects)
            processes["processor"] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "processor", "--port", "18155"],
                                                   cwd=ROOT, env=role_envs["processor"], folder=folder, label="processor-late", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["processor"], "http://127.0.0.1:18155/ready"); job.assert_listener(18155)
            chain.assert_above_cut_closed(fixture, analytics_db, folder, extra_subjects[1])
            print("PASS late at/below-cut invalidation and above-cut read closure", flush=True)

            subject = test_student["subject_ref"]
            if args.a010:
                a010_checks.wait_ready(analytics_db)
            if args.a011:
                a011_checks.wait_day_ready(analytics_db, projection_run)
            snapshot_subjects = [subject, historical_subject] if args.a010 else subject
            if args.a011:
                snapshot_subjects.append(day_fixture[0])
            day_revision = "a014" if args.a014 else "a012" if args.a012 else "a011"
            old_snapshot = snapshot(analytics_db, snapshot_subjects, proof=True, privacy_history=True,
                                    catalog_history=True, browser=args.a010, day_projection=args.a011,
                                    run_id=projection_run if args.a011 else None, day_revision=day_revision)
            if not old_snapshot["core_snapshot_proofs"] or not old_snapshot["catalog_versions"]:
                raise AssertionError("old snapshot lacks a008 catalog or a009 proof")
            restore_run = uuid4() if args.a010 else None
            if restore_run:
                r.write_new(folder / "restore-run.json", {"run_id": str(restore_run)})
            if args.a011:
                r.record(folder, token, restore_phase="transplant_pending", restore_target=restored_db["name"],
                         restore_old_run=str(projection_run), restore_new_run=str(restore_run))
            transplant_fresh(restored_db, old_snapshot, day_projection=args.a011,
                             old_run_id=projection_run if args.a011 else None,
                             new_run_id=restore_run if args.a011 else None, day_revision=day_revision)
            if args.a011:
                r.record(folder, token, restore_phase="transplant_complete")

            r.processes_stop([processes["core"]])
            chain.prepare_cutover(core_python, core_env, core_db)
            outbox_env = dict(analytics_core_env, PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE="outbox_only")
            processes["core"] = r.spawn_owned([str(core_python), "-B", "-m", "uvicorn", entrypoint, "--factory",
                                                "--host", "127.0.0.1", "--port", "18151", "--no-access-log"],
                                               cwd=CORE, env=outbox_env, folder=folder, label="core-outbox", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["core"], fixture["core_url"] + "/health/ready"); job.assert_listener(18151)
            chain.assert_profile_offload(fixture, core_db, baseline_profile)
            r.processes_stop([processes["query"]])
            chain.assert_profile_query_outage(fixture, core_db)
            processes["query"] = r.spawn_owned([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "query", "--port", "18153"],
                                               cwd=ROOT, env=proof_query_env, folder=folder, label="query-recovered", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["query"], "http://127.0.0.1:18153/ready"); job.assert_listener(18153)
            print("PASS admin profile/quota offload parity and Query outage before mutation", flush=True)
            replay_day = chain.assert_outbox_only_visits(fixture, core_db)
            deleted_evidence = None
            if args.rollback_read:
                a011_checks.wait_day_ready(analytics_db, projection_run)
                with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
                    assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days WHERE subject_ref=%s", (test_student["subject_ref"],)).fetchone()[0] > 0
                deleted_evidence = chain.delete_subject_with_proof(core_python, relay_env, fixture, core_db, analytics_db, folder, browser_payload=browser_payload, user=test_student, day_projection=True)
            r.processes_stop([processes["core"]])
            chain.replay_mirror(core_python, core_env, core_db, fixture, replay_day)
            if args.rollback_read:
                chain.assert_deleted_after_replay(fixture, analytics_db, folder, test_student, deleted_evidence, browser_payload)
            if args.rollback_read:
                processes["core"] = r.spawn_owned([str(core_python), "-B", "-m", "uvicorn", entrypoint, "--factory",
                                                    "--host", "127.0.0.1", "--port", "18151", "--no-access-log"],
                                                   cwd=CORE, env=core_env, folder=folder, label="core-rollback", children=children, job=job, on_spawn=on_spawn)
                r.wait_http(processes["core"], fixture["core_url"] + "/health/ready"); job.assert_listener(18151)
                chain.assert_legacy_rollback_read(fixture, core_db, replay_day)
                print("PASS replayed Core rows are visible through the legacy HTTP reader", flush=True)
                r.processes_stop([processes["core"]])
            processes["core"] = r.spawn_owned([str(core_python), "-B", "-m", "uvicorn", entrypoint, "--factory",
                                                "--host", "127.0.0.1", "--port", "18151", "--no-access-log"],
                                               cwd=CORE, env=analytics_core_env, folder=folder, label="core-mirror", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["core"], fixture["core_url"] + "/health/ready"); job.assert_listener(18151)
            reconcile_outbox(core_python, relay_env, core_db)
            if args.rollback_read:
                r.run_child([str(core_python), "-B", str(core_fixture), "proof-refresh-student"], cwd=CORE, env=relay_env, timeout=45)
                chain.assert_complete_query(fixture, core_db)
                chain.assert_returned_analytics_read(fixture)
                chain.assert_deleted_after_replay(fixture, analytics_db, folder, test_student, deleted_evidence, browser_payload)
                print("PASS returned Analytics Query and guarded Core HTTP read", flush=True)
            print("PASS guarded outbox-only writes and bounded replay to mirror", flush=True)
            if args.a011 and not args.rollback_read:
                a011_checks.wait_day_ready(analytics_db, projection_run)
                with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
                    assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days WHERE subject_ref=%s", (test_student["subject_ref"],)).fetchone()[0] > 0
            if not args.rollback_read:
                chain.delete_subject_with_proof(core_python, relay_env, fixture, core_db, analytics_db, folder, browser_payload=browser_payload, user=test_student, day_projection=args.a011)
            r.record(folder, token, state="restore_check_running", **({"restore_phase": "rebuild_pending"} if args.a011 else {}))
            restore_after_deletion(restored_db, restored_urls, common, roster, subject, folder, children, job, on_spawn,
                                   full_chain=True, activity_projection=args.a010, day_projection=args.a011, old_run_id=projection_run, new_run_id=restore_run,
                                   surviving_day=day_fixture if args.a011 else None)
            if args.a011:
                r.record(folder, token, restore_phase="rebuild_complete")
            print("PASS logical snapshot purge and journal barrier" if args.a010 else "PASS a007-a009 proof purge and logical snapshot journal barrier", flush=True)
        else:
            r.record(folder, token, state="restore_check_running")
            restore_after_deletion(restored_db, restored_urls, common, roster, subject, folder, children, job, on_spawn)
            print("PASS a007 logical restore privacy barrier and purge", flush=True)
        r.processes_stop([proxy])
        relay_env["PYMENTOR_ANALYTICS_TRANSFER_URL"] = fixture["collector_url"]
        if args.hold_seconds and not args.learner_course_review:
            if preserve_browser_student and args.a010:
                chain.observe_hold_overview(fixture, core_db, analytics_db, expect_current=False)
                r.run_child([str(core_python), "-B", str(core_fixture), "proof-refresh-student"],
                            cwd=CORE, env=relay_env, timeout=45)
                chain.observe_hold_overview(fixture, core_db, analytics_db, expect_current=True)
                print("PASS current signed proof vector and global/course overview before hold", flush=True)
                r.record(folder, token, state="processes_started", processes=[p.pid for p in children])
                extra_version = chain.hold_extra_catalog(core_python, core_env, folder, token, fixture)
                chain.hold_cross_course_baseline(fixture, analytics_db, extra_version)
                if args.browser_pages:
                    from cross_service.browser_pages import seed_and_verify
                    seed_and_verify(fixture, analytics_db)
            verify_browser_roles(fixture)
            hold_processes = [authority_process, *processes.values()] + ([catalog_proxy] if catalog_proxy else [])
            browser_hold(folder, token, args.hold_seconds, hold_processes)
            reconcile_outbox(core_python, relay_env, core_db)
        if args.publisher:
            from cross_service import publisher_checks
            publisher_env = dict(relay_env, PYMENTOR_ANALYTICS_PUBLISHER_ENABLED="1",
                                 PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE="1",
                                 PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE=core_db["name"],
                                 PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER=core_db["owner"])
            if args.publisher_image:
                from cross_service.docker_publisher import DockerPublisher
                publisher_container = DockerPublisher(args.publisher_image, folder, token, publisher_env, fixture, core_db, authority_url)
                publisher_container.prepare()
            publisher_checks.exercise(core_python, publisher_env, folder, fixture, core_db, analytics_db,
                                      children, job, on_spawn, container=publisher_container)
            if publisher_container:
                print("PASS Linux PID 1 SIGTERM and docker wait exit 0 with publisher_stopped", flush=True)
        if args.chain:
            # Run the destructive synthetic ID-reuse race after browser release,
            # so the review's previously verified roster stays stable throughout.
            race_port = r.unused_port()
            race_env = dict(env, PYTHONPATH=str(ROOT / "tests"), CROSS_SERVICE_PROFILE_PROXY_PORT=str(race_port))
            race_proxy = r.spawn_owned([sys.executable, "-B", str(ROOT / "tests/cross_service/profile_race_proxy.py")],
                                      cwd=ROOT, env=race_env, folder=folder, label="profile-race-proxy", children=children, job=job, on_spawn=on_spawn)
            r.wait_port(race_proxy, race_port); job.assert_listener(race_port)
            r.processes_stop([processes["core"]])
            race_core_env = dict(analytics_core_env, PYMENTOR_ANALYTICS_QUERY_URL=f"http://127.0.0.1:{race_port}")
            processes["core"] = r.spawn_owned([str(core_python), "-B", "-m", "uvicorn", entrypoint, "--factory",
                                                "--host", "127.0.0.1", "--port", "18151", "--no-access-log"],
                                               cwd=CORE, env=race_core_env, folder=folder, label="core-profile-race", children=children, job=job, on_spawn=on_spawn)
            r.wait_http(processes["core"], fixture["core_url"] + "/health/ready"); job.assert_listener(18151)
            chain.assert_profile_reused_id_race(fixture, core_db, folder)
            r.processes_stop([race_proxy])
            print("PASS admin quota rejects buffered stale response after synthetic numeric ID reuse", flush=True)
        if memory_short.is_set():
            raise RuntimeError("memory headroom fell below 1536 MiB")
        r.record(folder, token, state="switch_rehearsal_passed" if args.a014 else "acceptance_passed")
        successful = True
    except Exception as exc:
        try:
            bytes_new(folder / "failure.log", traceback.format_exc().encode("utf-8"))
        except OSError:
            pass
        detail = {"error_type": type(exc).__name__}
        if isinstance(exc, ModuleNotFoundError):
            detail["error_module"] = exc.name
        r.record(folder, token, state="reconciliation_required", **detail)
        print("Synthetic run incomplete; owned resources preserved for reconciliation: " + folder.name, file=sys.stderr)
        return 1
    finally:
        monitor_stop.set()
        if monitor is not None:
            monitor.join(timeout=3)
        if memory_short.is_set():
            successful = False
            r.record(folder, token, state="memory_reconciliation_required")
        stopped = True
        if publisher_container is not None:
            try:
                publisher_container.close()
            except Exception:
                stopped = False
        try:
            r.processes_stop(children)
        except Exception:
            stopped = False
        if memory_short.is_set():
            if job is not None:
                job.close(); r.set_job(None)
            print("Memory headroom guard stopped owned processes; reconcile private record:", folder.name, file=sys.stderr)
            return 1
        if not stopped:
            if job is not None:
                job.close(); r.set_job(None)
            r.record(folder, token, state="process_reconciliation_required")
            print("Owned process shutdown requires reconciliation:", folder.name, file=sys.stderr)
            return 1
        if successful:
            try:
                r.record(folder, token, state="redis_cleanup_pending")
                r.run_child([str(core_python), "-B", str(core_fixture), "clean-redis"], cwd=CORE, env=core_env, timeout=15)
                r.record(folder, token, state="core_cleanup_pending")
                r.cleanup_core(admin, core_db)
                r.record(folder, token, state="analytics_cleanup_pending")
                r.cleanup_analytics(analytics_data, analytics_db)
                r.record(folder, token, state="restore_cleanup_pending")
                r.cleanup_analytics(restored_data, restored_db)
                r.record(folder, token, state="cleanup_complete")
                if job is not None:
                    job.close(); r.set_job(None)
                r.cleanup_private(folder, token)
                result = "Synthetic a014 switch and logical restore rehearsal complete" if args.a014 else "Synthetic acceptance complete"
                print(result + "; cleaned owned databases:", core_db["name"], analytics_db["name"], restored_db["name"])
            except Exception as exc:
                if job is not None:
                    job.close(); r.set_job(None)
                r.record(folder, token, state="cleanup_reconciliation_required", error_type=type(exc).__name__)
                print("Synthetic cleanup incomplete; reconcile private record:", folder.name, file=sys.stderr)
                return 1
        elif job is not None:
            job.close(); r.set_job(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
