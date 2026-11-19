"""Opt-in autonomous publisher checks against the owned synthetic fixture."""
import time

import httpx
import psycopg

from . import resources as r
from .chain_checks import login, observe_hold_overview


def start_publisher(python, env, folder, label, children, job, on_spawn):
    process = r.spawn_owned([str(python), "-B", "-m", "pymentor_core.app.analytics_publisher"],
                            cwd=r.CORE, env=env, folder=folder, label=label,
                            children=children, job=job, on_spawn=on_spawn)
    log = folder / (label + ".log")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("owned publisher exited before readiness")
        if "publisher_ready" in log.read_text(encoding="utf-8"):
            return process
        time.sleep(.1)
    raise TimeoutError("owned publisher readiness deadline")


def wait_recovery(fixture, core_db, analytics_db, publisher, *, event_id=None, deadline_seconds=100, poll=None):
    user = fixture["users"]["student"]
    deadline = time.monotonic() + deadline_seconds
    with httpx.Client(base_url=fixture["core_url"], timeout=3, trust_env=False) as admin, \
         httpx.Client(base_url=fixture["core_url"], timeout=3, trust_env=False) as student:
        login(admin, fixture["users"]["admin"]); login(student, user)
        while time.monotonic() < deadline:
            if (poll(publisher) if poll else publisher.poll()) is not None:
                raise RuntimeError("owned publisher exited during recovery")
            with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
                revision = conn.execute("SELECT revision FROM analytics_transfer_revisions WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0]
                delivery = (conn.execute("SELECT status FROM analytics_transfer_outbox WHERE event_id=%s", (event_id,)).fetchone()
                            if event_id else ("delivered",))
            with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
                proof = conn.execute("""SELECT cut_revision,subject_epoch,COALESCE(f.epoch,0),
                    EXISTS(SELECT 1 FROM analytics.core_transfer_projection q WHERE q.producer_namespace='core'
                      AND q.subject_ref=p.subject_ref AND q.revision>p.cut_revision)
                    FROM analytics.core_snapshot_proofs p LEFT JOIN analytics.subject_fences f ON f.subject_ref=p.subject_ref
                    WHERE p.producer_namespace='core' AND p.subject_ref=%s AND p.status='verified'""", (user["subject_ref"],)).fetchone()
                facts = (conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE producer_namespace='core' AND event_id=%s AND subject_ref=%s", (event_id, user["subject_ref"])).fetchone()[0]
                         if event_id else 1)
                projections = (conn.execute("SELECT count(*) FROM analytics.core_transfer_projection WHERE producer_namespace='core' AND event_id=%s AND subject_ref=%s", (event_id, user["subject_ref"])).fetchone()[0]
                               if event_id else 1)
            if delivery == ("delivered",) and proof == (revision, 0, 0, False) and facts == projections == 1:
                whole = admin.get("/api/admin-platform/overview")
                profile = admin.get(f"/api/admin/users/{user['id']}/profile", params={"year": 2026, "month": 11})
                calendar = student.get("/api/user/activity", params={"year": 2026, "month": 11})
                if (whole.status_code == profile.status_code == calendar.status_code == 200
                        and all(response.headers.get("x-analytics-coverage") == "complete" for response in (whole, profile, calendar))):
                    return revision
            time.sleep(.5)
    raise TimeoutError("publisher did not converge before deadline")


def exercise(python, env, folder, fixture, core_db, analytics_db, children, job, on_spawn, *, container=None):
    """No manual relay or stage after publisher starts or after the mutation."""
    observe_hold_overview(fixture, core_db, analytics_db, expect_current=False, expected_ids=(1, 2, 3, 4, 5))
    start = container.start if container else lambda label: start_publisher(python, env, folder, label, children, job, on_spawn)
    stop = container.stop if container else lambda process: r.processes_stop([process])
    poll = container.poll if container else None
    first = start("publisher-first")
    old_revision = wait_recovery(fixture, core_db, analytics_db, first, poll=poll)
    observe_hold_overview(fixture, core_db, analytics_db, expect_current=True, expected_ids=(1, 2, 3, 4, 5))
    print("PASS publisher autonomously repaired stale proof and global/self/profile reads", flush=True)
    # The native Windows path remains a forced stop; the container path verifies SIGTERM.
    stop(first)
    user = fixture["users"]["student"]
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as student, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(student, user); login(admin, fixture["users"]["admin"])
        result = student.post("/api/user/activity/visit", json={"activity_date": "2026-11-19"})
        if result.status_code != 200:
            raise RuntimeError("synthetic activity mutation failed; reconcile before retry")
        with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
            revision = conn.execute("SELECT revision FROM analytics_transfer_revisions WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0]
            rows = conn.execute("""SELECT event_id,status FROM analytics_transfer_outbox
                WHERE subject_ref=%s AND kind='legacy_activity_day' AND payload::jsonb->'payload'->>'activity_date'='2026-11-19'""", (user["subject_ref"],)).fetchall()
        assert revision > old_revision and len(rows) == 1 and rows[0][1] == "pending"
        event_id = rows[0][0]
        assert admin.get("/api/admin-platform/overview").status_code == 503
        assert admin.get(f"/api/admin/users/{user['id']}/profile", params={"year": 2026, "month": 11}).status_code == 503
        assert student.get("/api/user/activity", params={"year": 2026, "month": 11}).status_code == 503
    second = start("publisher-restart")
    recovered_revision = wait_recovery(fixture, core_db, analytics_db, second, event_id=event_id, poll=poll)
    assert recovered_revision == revision
    observe_hold_overview(fixture, core_db, analytics_db, expect_current=True, expected_ids=(1, 2, 3, 4, 5))
    stop(second)
    print("PASS publisher restart recovered one committed offline mutation with one fact and projection", flush=True)
