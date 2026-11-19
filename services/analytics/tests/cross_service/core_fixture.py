"""Executed by Core's Python in an already-created synthetic target only."""
import json
import os
from pathlib import Path
import shutil
import sys


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_courses(folder):
    from pymentor_core.adapters.course_local.packages import load_course_manifest
    from pymentor_core.adapters.course_local.runtime import CourseRuntime, content_version
    root = folder / "courses"
    package = root / "backend"
    for part in ("backend/plans/chapter_01/block_01", "backend/practice", "frontend"):
        (package / part).mkdir(parents=True, exist_ok=False)
    (package / "backend/practice/__init__.py").write_text("", encoding="utf-8")
    (package / "backend/plans/chapter_01/block_01/01 - Как запускается программа.md").write_text("# Synthetic lesson\n", encoding="utf-8")
    for name in ("course", "personal", "landing"):
        (package / f"frontend/{name}.ts").write_text("export {};\n", encoding="utf-8")
    repository = Path(__file__).resolve().parents[4]
    shutil.copytree(repository / "courses/shared/backend", root / "shared/backend")
    practice = []
    if os.environ.get("CROSS_SERVICE_BROWSER_PRACTICE") == "1":
        import hashlib
        task = {"id": "lesson-1-task-01", "title": "Удвоить число", "level": "easy", "mode": "solve",
                "prompt": "Напишите функцию solve(number), которая возвращает число, умноженное на два.",
                "starter_code": "def solve(number):\n    # Верните удвоенное число\n    pass\n",
                "hints": ["Используйте умножение."], "requirements": {},
                "tests": [{"name": "пример", "args": [3], "expected": 6}]}
        task["revision"] = hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        practice = [task]
    catalog = '''from .catalog_api import Catalog
TRACKS = [{"id": "synthetic-track", "title": "Synthetic track", "order": 1, "tagline": "Fixture", "lessons": [
    {"id": "lesson-1", "track": "synthetic-track", "module": "Fixture", "title": "Synthetic lesson", "access": "free",
     "source_file": "chapter_01/block_01/01 - Как запускается программа.md", "tasks": PRACTICE, "self_check": False, "manual_practice": [],
     "progress_policy": {"overview": True}}
]}]
globals().update(Catalog(TRACKS).exports())
'''
    (package / "backend/catalog.py").write_text(catalog.replace("PRACTICE", repr(practice)), encoding="utf-8")
    manifest = {"schema_version": 2, "course_id": "backend", "slug": "backend", "title": "Synthetic course",
                "content_version": "0"*64, "leader_user_id": 2,
                "layout": {"plans": "backend/plans", "practice": "backend/practice", "frontend": "frontend"},
                "frontend": {"course": "frontend/course.ts", "personal_landing": "frontend/personal.ts", "course_landing": "frontend/landing.ts"},
                "tracks": [{"track_id": "synthetic-track", "title": "Synthetic track", "order": 1, "source_dirs": ["chapter_01"]}]}
    write(package / "course.yaml", manifest)
    courses = [{"course_id": "backend", "enabled": True, "manifest": "backend/course.yaml"}]
    if os.environ.get("CROSS_SERVICE_BROWSER_EXTRA") == "1":
        extra = root / "backend_extra"
        shutil.copytree(package, extra)
        plans = extra / "backend/plans/chapter_01/block_01"
        (plans / "01 - Как запускается программа.md").rename(plans / "02 - Второй курс.md")
        extra_catalog = (catalog.replace("synthetic-track", "extra-track").replace("lesson-1", "extra-lesson-1")
                         .replace("01 - Как запускается программа.md", "02 - Второй курс.md").replace("PRACTICE", "[]"))
        (extra / "backend/catalog.py").write_text(extra_catalog, encoding="utf-8")
        extra_manifest = dict(manifest, course_id="backend_extra", slug="backend_extra", title="Synthetic extra course",
                              leader_user_id=None, tracks=[{"track_id": "extra-track", "title": "Extra track", "order": 1, "source_dirs": ["chapter_01"]}])
        write(extra / "course.yaml", extra_manifest)
        extra_manifest["content_version"] = content_version(extra, load_course_manifest(extra))
        write(extra / "course.yaml", extra_manifest)
        courses.append({"course_id": "backend_extra", "enabled": True, "manifest": "backend_extra/course.yaml"})
    if os.environ.get("CROSS_SERVICE_BROWSER_PAGES") == "1":
        additions = []
        for index in range(1, 26):
            lesson_id = f"fixture-page-{index:02d}"
            source_file = f"chapter_01/block_01/page-{index:02d}.md"
            (package / "backend/plans" / source_file).write_text("# Synthetic report fixture\n", encoding="utf-8")
            additions.append({"id": lesson_id, "track": "synthetic-track", "module": "Fixture",
                              "title": f"Synthetic report lesson {index:02d}", "access": "free",
                              "source_file": source_file, "tasks": [], "self_check": False,
                              "manual_practice": [], "progress_policy": {"overview": True}})
        extended = catalog.replace("PRACTICE", repr(practice)).replace(
            "globals().update(Catalog(TRACKS).exports())",
            "TRACKS[0]['lessons'].extend(" + repr(additions) + ")\nglobals().update(Catalog(TRACKS).exports())")
        (package / "backend/catalog.py").write_text(extended, encoding="utf-8")
    write(root / "registry.json", {"schema_version": 1, "courses": courses})
    manifest["content_version"] = content_version(package, load_course_manifest(package))
    write(package / "course.yaml", manifest)
    CourseRuntime(root).snapshot("backend")
    if len(courses) == 2:
        CourseRuntime(root).snapshot("backend_extra")


def seed(folder):
    from sqlalchemy import create_engine, text
    from pymentor_core.modules.identity.passwords import hash_password
    from tools.maintenance.course_leader_config import reconcile
    fixture = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
    db = fixture["databases"]["core"]
    engine = create_engine(db["dsn"].replace("postgresql://", "postgresql+psycopg://", 1),
                           connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000 -c lock_timeout=1000"})
    try:
        with engine.begin() as conn:
            actual = conn.execute(text("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")).one()
            sentinel = conn.execute(text("SELECT token FROM public.cross_service_sentinel")).scalars().all()
            if tuple(actual) != (db["name"], db["owner"]) or sentinel != [db["sentinel"]]:
                raise ValueError("Core synthetic guard failed")
            for group in ("users", "browser_users"):
                for name, user in fixture[group].items():
                    if group == "browser_users" and name == "leader":
                        continue
                    conn.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (:id,:name,:email,:password,true,:admin)"),
                                 {"id": user["id"], "name": ("browser-" if group == "browser_users" else "") + name, "email": user["email"], "password": hash_password(user["password"]), "admin": name == "admin" and not (group == "browser_users" and os.environ.get("CROSS_SERVICE_DELETE_SEPARATE_STUDENT") == "1")})
                    conn.execute(text("INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:id,:ref)"),
                                 {"id": user["id"], "ref": user["subject_ref"]})
            assert reconcile(conn, {"backend": fixture["users"]["leader"]["id"]}, apply=True)[0]["status"] == "applied"
            conn.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id) VALUES (10,'2099-01-10','10:00',60,'backend',2),(11,'2099-01-11','10:00',60,'backend',2)"))
            conn.execute(text("INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at) VALUES (10,'backend',3,2,'booked','2099-01-10T10:00:00Z','2099-01-10T11:00:00Z'),(11,'backend',7,2,'booked','2099-01-11T10:00:00Z','2099-01-11T11:00:00Z')"))
            if os.environ.get("CROSS_SERVICE_LEARNER_COURSE_REVIEW") == "1":
                conn.execute(text("""INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id)
                    VALUES (13,date_trunc('month',now() AT TIME ZONE 'Europe/Moscow')::date,'09:00',60,'backend',2)"""))
                conn.execute(text("""INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at)
                    SELECT 13,'backend',3,2,'completed',starts_at,starts_at+interval '1 hour'
                    FROM (SELECT (date_trunc('month',now() AT TIME ZONE 'Europe/Moscow')+interval '9 hours') AT TIME ZONE 'Europe/Moscow' AS starts_at) AS day"""))
                conn.execute(text("""INSERT INTO booking_quotas(user_id,year,month,granted_slots,course_id)
                    SELECT 3,extract(year FROM day),extract(month FROM day),1,'backend'
                    FROM (SELECT now() AT TIME ZONE 'Europe/Moscow' AS day) AS today"""))
                # An imported and a measured day can coincide. Opening the browser
                # in parallel mode must also be an idempotent legacy visit.
                conn.execute(text("""INSERT INTO user_activity_days(user_id,activity_date,visited_at)
                    SELECT id,(now() AT TIME ZONE 'Europe/Moscow')::date,now() FROM users WHERE id IN (2,3)"""))
            if os.environ.get("CROSS_SERVICE_DELETE_SEPARATE_STUDENT") == "1":
                conn.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id) VALUES (12,'2099-01-12','10:00',60,'backend',2)"))
                conn.execute(text("INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at) VALUES (12,'backend',5,2,'booked','2099-01-12T10:00:00Z','2099-01-12T11:00:00Z')"))
            conn.execute(text("INSERT INTO user_activity_days(user_id,activity_date,visited_at) VALUES (3,'2026-11-11',NULL),(3,'2026-11-13',NULL),(3,'2026-11-15',NULL),(3,'2026-11-16','2026-11-16T23:30:00Z')"))
            conn.execute(text("INSERT INTO user_activity_days(user_id,activity_date,visited_at) VALUES (1,(now() AT TIME ZONE 'Europe/Moscow')::date,now()),(4,(now() AT TIME ZONE 'Europe/Moscow')::date,now())"))
            conn.execute(text("INSERT INTO user_lesson_progress(user_id,course_id,lesson_id,completed_at) VALUES (3,'backend','lesson-1','2026-11-16T21:30:00Z')"))
            if os.environ.get("PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED") == "1":
                conn.execute(text("""INSERT INTO user_task_progress(user_id,course_id,task_id,task_revision,completed_at)
                    VALUES (3,'backend','fixture-historical-task','r1','2026-11-16T21:31:00Z')"""))
    finally:
        engine.dispose()


def clean_redis():
    import re
    import time
    from redis import Redis
    prefix = os.environ["KNOWI_REDIS_PREFIX"]
    if not re.fullmatch(r"cross-service-[0-9a-f]{32}", prefix) or os.environ.get("REDIS_URL") != "redis://127.0.0.1:57381/13":
        raise ValueError("invalid disposable Redis target")
    redis = Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2, socket_connect_timeout=2)
    try:
        cursor, deadline = 0, time.monotonic() + 5
        for _ in range(100):
            if time.monotonic() >= deadline:
                raise TimeoutError("bounded Redis cleanup deadline")
            cursor, keys = redis.scan(cursor=cursor, match=prefix + "*", count=100)
            if any(not key.decode("utf-8").startswith(prefix) for key in keys):
                raise ValueError("Redis prefix mismatch")
            if keys:
                redis.delete(*keys)
            if cursor == 0:
                break
        else:
            raise TimeoutError("bounded Redis scan exhausted")
        cursor = 0
        for _ in range(100):
            if time.monotonic() >= deadline:
                raise TimeoutError("bounded Redis verification deadline")
            cursor, keys = redis.scan(cursor=cursor, match=prefix + "*", count=100)
            if keys:
                raise RuntimeError("owned Redis keys remain")
            if cursor == 0:
                return
        raise TimeoutError("bounded Redis scan exhausted")
    finally:
        redis.close()


def proof_batch(folder, *, wanted):
    """Use one owned Core process for bounded proof staging or receipt polling."""
    import time
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from pymentor_core.adapters.sql.analytics_delivery_ops import export
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import stage_subject
    from tools.analytics_transfer.run import _relay
    fixture = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
    target = fixture["databases"]["core"]
    engine = create_engine(target["dsn"].replace("postgresql://", "postgresql+psycopg://", 1), poolclass=NullPool,
                           connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000 -c lock_timeout=1000"})
    try:
        with engine.connect() as conn:
            identity = conn.execute(text("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")).one()
            marker = conn.execute(text("SELECT token FROM public.cross_service_sentinel")).scalars().all()
            if tuple(identity) != (target["name"], target["owner"]) or marker != [target["sentinel"]]:
                raise ValueError("Core proof batch target guard failed")
        sessions = sessionmaker(engine)
        if wanted == "write-task":
            from pymentor_core.adapters.sql.progress import mark_task_completed
            user = fixture["users"]["student"]
            for revision in ("r2", "r2"):
                with sessions() as db:
                    mark_task_completed(user["id"], "fixture-historical-task", revision, db,
                                        course_id="backend", expected_account_ref=user["subject_ref"])
            return
        if wanted == "refresh-student":
            user = fixture["users"]["student"]
            with sessions() as db:
                staged = stage_subject(db, user["id"], user["subject_ref"])
                db.commit()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                _relay(sessions, proofs_only=True)
                with sessions() as db:
                    row = db.execute(text("""SELECT status,accepted FROM analytics_transfer_snapshot_proofs
                        WHERE subject_ref=:ref AND cut_revision=:cut"""),
                        {"ref": user["subject_ref"], "cut": staged["cut_revision"]}).one_or_none()
                if row == ("verified", True):
                    return
                if row and row[0] == "quarantined":
                    raise ValueError("current synthetic proof quarantined")
                time.sleep(.25)
            raise TimeoutError("current synthetic proof did not verify")
        if wanted == "stage":
            streams = ("activity", "progress", "tasks") if os.environ.get("PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED") == "1" else ("activity", "progress")
            for stream in streams:
                with sessions() as db:
                    result = export.export_page(db, stream, limit=100)
                    if not result["scan_exhausted"]:
                        raise ValueError("synthetic backfill exceeds page bound")
                    export.seal_backfill(db, stream)
                    db.commit()
            with sessions() as db:
                subjects = db.execute(text("SELECT u.id,a.lifecycle_id FROM users u JOIN account_lifecycles a ON a.user_id=u.id ORDER BY u.id")).all()
            if len(subjects) != 6:
                raise ValueError("unexpected synthetic roster")
            for uid, ref in subjects:
                with sessions() as db:
                    stage_subject(db, uid, ref)
                    db.commit()
            return
        if wanted not in {"accepted", "verified"}:
            raise ValueError("invalid proof batch goal")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _relay(sessions, proofs_only=True)
            with sessions() as db:
                if wanted == "accepted":
                    count = db.execute(text("SELECT count(*) FROM analytics_transfer_snapshot_proofs WHERE accepted AND status='pending'")).scalar_one()
                else:
                    count = db.execute(text("SELECT count(*) FROM analytics_transfer_snapshot_proofs WHERE status='verified'")).scalar_one()
                quarantined = db.execute(text("SELECT count(*) FROM analytics_transfer_snapshot_proofs WHERE status='quarantined'")).scalar_one()
            if quarantined:
                raise ValueError("synthetic proof quarantined")
            if count == 6:
                return
            time.sleep(.25)
        raise TimeoutError("bounded proof batch did not converge")
    finally:
        engine.dispose()


if __name__ == "__main__":
    folder = Path(os.environ["CROSS_SERVICE_PRIVATE_DIR"]).resolve(strict=True)
    root = (Path(__file__).resolve().parents[2] / ".local" / "cross-service").resolve(strict=True)
    if folder.parent != root or len(folder.name) != 32 or any(c not in "0123456789abcdef" for c in folder.name):
        raise SystemExit("invalid private fixture directory")
    if sys.argv[1:] == ["courses"]:
        prepare_courses(folder)
    elif sys.argv[1:] == ["seed"]:
        seed(folder)
    elif sys.argv[1:] == ["clean-redis"]:
        clean_redis()
    elif sys.argv[1:] in (["proof-stage"], ["proof-accepted"], ["proof-verified"], ["proof-refresh-student"], ["proof-write-task"]):
        proof_batch(folder, wanted=sys.argv[1].removeprefix("proof-"))
    else:
        raise SystemExit("invalid fixture operation")
