"""Runs only in the isolated legacy smoke container; prints no source data."""
import http.client
import hashlib
import importlib.metadata
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

_phase = "configuration"
_server_hint = "server_error"


def classify_server_error(data):
    value = data.lower()
    for needle, code in ((b"permission denied", "permission_denied"),
                         (b"read-only file system", "read_only_filesystem"),
                         (b"no module named", "missing_module"),
                         (b"unable to open database file", "database_open_failed"),
                         (b"no such table", "missing_table")):
        if needle in value:
            return code
    return "server_error"


def request(method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", 18791, timeout=10)
    headers = {"Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        body = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = "http://127.0.0.1:18791"
    try:
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("response too large")
        return response.status, response.getheader("Set-Cookie"), json.loads(data) if data else None
    finally:
        conn.close()


def _login(email, password):
    status, cookie, _ = request("POST", "/api/user/login", {"email": email, "password": password})
    if status != 200 or not cookie or not cookie.startswith("session="):
        raise RuntimeError("synthetic login failed")
    return cookie.split(";", 1)[0]


def _seed_on_copy():
    # This exact path is the only writable database mounted into the container.
    if os.environ.get("DATABASE_URL") != "sqlite:////data/test.db":
        raise RuntimeError("unexpected database identity")
    from pymentor_core.modules.identity.passwords import hash_password
    db = sqlite3.connect("/data/test.db")
    try:
        historical_source = db.execute("""SELECT u.id FROM users u WHERE
            EXISTS(SELECT 1 FROM user_activity_days a WHERE a.user_id=u.id)
            AND EXISTS(SELECT 1 FROM user_lesson_progress p WHERE p.user_id=u.id)
            ORDER BY u.id LIMIT 1""").fetchone()
        if historical_source is None:
            raise RuntimeError("historical calendar/progress fixture unavailable")
        original_id = historical_source[0]
        nonce, password = secrets.token_hex(12), secrets.token_urlsafe(24)
        credentials = []
        for role in (False, True):
            email = f"smoke-{nonce}-{'admin' if role else 'student'}@example.invalid"
            username = f"smoke-{nonce}-{'a' if role else 's'}"
            cur = db.execute("INSERT INTO users(username,email,password_hash,is_admin,verified) VALUES(?,?,?,?,1)",
                             (username, email, hash_password(password), int(role)))
            credentials.append((email, cur.lastrowid))
        target = credentials[0][1]
        db.execute("""INSERT INTO user_activity_days(user_id,activity_date,visited_at)
                   SELECT ?,activity_date,visited_at FROM user_activity_days WHERE user_id=?""", (target, original_id))
        db.execute("""INSERT INTO user_lesson_progress(user_id,lesson_id,completed_at)
                   SELECT ?,lesson_id,completed_at FROM user_lesson_progress WHERE user_id=?""", (target, original_id))
        db.execute("""INSERT INTO user_task_progress(user_id,task_id,task_revision,completed_at)
                   SELECT ?,task_id,task_revision,completed_at FROM user_task_progress WHERE user_id=?""", (target, original_id))
        date_value = db.execute("SELECT activity_date FROM user_activity_days WHERE user_id=? ORDER BY activity_date LIMIT 1", (target,)).fetchone()[0]
        year, month = (int(value) for value in date_value[:7].split("-"))
        expected_days = [x[0] for x in db.execute("SELECT activity_date FROM user_activity_days WHERE user_id=? AND substr(activity_date,1,7)=? ORDER BY activity_date", (target, date_value[:7]))]
        lessons = {x[0] for x in db.execute("SELECT lesson_id FROM user_lesson_progress WHERE user_id=?", (target,))}
        students = db.execute("SELECT count(*) FROM users WHERE NOT is_admin").fetchone()[0]
        db.commit()
        return credentials, password, target, year, month, expected_days, lessons, students
    finally:
        db.close()


def dependency_fingerprint():
    from pathlib import Path
    requirements = Path("/legacy-requirements.txt").read_text(encoding="utf-8")
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version = ("a2wsgi", "1.10.10") if line == "a2wsgi>=1.10" else line.split("==", 1)
        if importlib.metadata.version(name) != version:
            raise RuntimeError("frozen dependency mismatch")
    return hashlib.sha256(Path("/legacy-dependencies.txt").read_bytes()).hexdigest()


def run():
    global _phase, _server_hint
    _phase = "configuration"
    if os.environ.get("PYMENTOR_PROFILE") != "legacy_sqlite" or os.environ.get("PYTHON_DOTENV_DISABLED") != "1" or os.environ.get("PYMENTOR_LOCAL_NOTIFICATIONS") != "0":
        raise RuntimeError("unsafe runtime configuration")
    _phase = "dependencies"
    dependency_digest = dependency_fingerprint()
    _phase = "service_start"
    server_stderr = bytearray()
    server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "pymentor_core.app.asgi:create_app",
                               "--factory", "--host", "127.0.0.1", "--port", "18791"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def drain_stderr():
        while True:
            chunk = server.stderr.read(8192)
            if not chunk:
                break
            server_stderr.extend(chunk)
            if len(server_stderr) > 65536:
                del server_stderr[:-65536]
    reader = None
    if getattr(server, "stderr", None) is not None:
        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()
    try:
        _phase = "service_ready"
        deadline = time.monotonic() + 45
        while True:
            if server.poll() is not None:
                raise RuntimeError("legacy service exited")
            try:
                if request("GET", "/health/ready")[0] == 200:
                    break
            except (OSError, ValueError, http.client.HTTPException):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("legacy service not ready")
            time.sleep(.2)
        _phase = "seed_copy"
        actors, password, student_id, year, month, days, lesson_ids, total = _seed_on_copy()
        _phase = "login"
        student, admin = (_login(actor[0], password) for actor in actors)
        _phase = "negative_auth"
        if request("GET", "/api/admin/overview")[0] != 401 or request("GET", "/api/admin/overview", cookie=student)[0] != 403:
            raise RuntimeError("role checks failed")
        _phase = "admin_overview"
        status, _, overview = request("GET", "/api/admin/overview", cookie=admin)
        if status != 200 or overview.get("total_students") != total:
            raise RuntimeError("admin overview mismatch")
        _phase = "calendar"
        period = urlencode({"year": year, "month": month})
        status, _, calendar = request("GET", "/api/user/activity?" + period, cookie=student)
        if status != 200 or calendar.get("days") != days:
            raise RuntimeError("calendar mismatch")
        _phase = "summary"
        if request("GET", "/api/user/activity/summary", cookie=student)[0] != 200:
            raise RuntimeError("summary failed")
        _phase = "admin_profile"
        status, _, result = request("GET", f"/api/admin/users/{student_id}/profile?" + period, cookie=admin)
        if status != 200 or result["profile"]["activity_days"] != days:
            raise RuntimeError("profile calendar mismatch")
        tracks = result["profile"]["learning_progress"]
        admin_lessons = {lesson["id"]: lesson["completed"] for track in tracks for module in track["modules"]
                         for lesson in module["lessons"] if lesson["id"] in lesson_ids}
        _phase = "catalog"
        status, _, catalog = request("GET", "/api/learning/catalog", cookie=student)
        if status != 200:
            raise RuntimeError("student progress endpoint failed")
        student_lessons = {lesson["id"]: lesson["completed"] for lessons in catalog["courses"].values()
                           for lesson in lessons if lesson["id"] in lesson_ids}
        _phase = "progress"
        matched = admin_lessons.keys() & student_lessons.keys()
        if not matched or any(admin_lessons[key] != student_lessons[key] for key in matched):
            raise RuntimeError("historical progress mismatch")
        print(json.dumps({"status": "PASS", "http": True, "calendar": True, "summary": True,
                          "admin": True, "progress": True, "negative_auth": True,
                          "dependency_sha256": dependency_digest}, separators=(",", ":")), flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        if reader is not None:
            reader.join(timeout=2)
        _server_hint = classify_server_error(server_stderr)


def main():
    try:
        run()
    except Exception as exc:
        error = "sqlite_error" if isinstance(exc, sqlite3.Error) else "runtime_error"
        if _phase in {"service_start", "service_ready"}:
            error = _server_hint
        print(json.dumps({"status": "FAIL", "phase": _phase, "error": error}, separators=(",", ":")), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
