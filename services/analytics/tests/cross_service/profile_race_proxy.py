"""Single guarded test proxy: replace one empty synthetic account after Query reply."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import psycopg
from cross_service.guards import load, verify_databases

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(os.environ["CROSS_SERVICE_PRIVATE_DIR"]).resolve(strict=True)
if FOLDER.parent != (ROOT / ".local/cross-service").resolve(strict=True) or not re.fullmatch(r"[0-9a-f]{32}", FOLDER.name):
    raise ValueError("invalid owned private fixture")
FIXTURE = load(str(FOLDER / "fixture.json"), ROOT)
owner = json.loads((FOLDER / "owner.json").read_text(encoding="utf-8"))
if owner.get("core_name") != FIXTURE["databases"]["core"]["name"] or owner.get("core_sentinel") != FIXTURE["databases"]["core"]["sentinel"]:
    raise ValueError("private owner does not match race target")
verify_databases(FIXTURE)
TARGET = FIXTURE["databases"]["core"]
UID = FIXTURE["users"]["other"]["id"]
if UID != 4:
    raise ValueError("unexpected synthetic race target")
PORT = int(os.environ["CROSS_SERVICE_PROFILE_PROXY_PORT"])
if not 1024 <= PORT <= 65535:
    raise ValueError("invalid loopback proxy port")
MARKER = FOLDER / "profile-race.json"
conn = psycopg.connect(TARGET["dsn"], connect_timeout=3, options="-c statement_timeout=500 -c lock_timeout=300")
with conn.transaction():
    row = conn.execute("SELECT u.username,u.email,a.lifecycle_id FROM users u JOIN account_lifecycles a ON a.user_id=u.id WHERE u.id=%s", (UID,)).fetchone()
    if row is None or row[:2] != ("replacement", "replacement@example.test") or row[2] == FIXTURE["users"]["other"]["subject_ref"]:
        raise ValueError("race target is not the empty replacement")
    OLD = row[2]
    if conn.execute("SELECT count(*) FROM booking_quotas WHERE user_id=%s", (UID,)).fetchone()[0]:
        raise ValueError("race target unexpectedly has quota")


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        if parsed.path != "/internal/analytics/v1/legacy/admin/profile" or params.get("subject_ref") != [OLD] or MARKER.exists():
            self.send_error(400)
            return
        upstream = http.client.HTTPConnection("127.0.0.1", 18153, timeout=.6)
        try:
            upstream.request("GET", self.path, headers={"Authorization": self.headers.get("Authorization", ""), "Accept": "application/json"})
            response = upstream.getresponse()
            data = response.read(65537)
            if response.status != 200 or len(data) > 65536:
                self.send_error(502)
                return
            new_ref = str(uuid4())
            with conn.transaction():
                if conn.execute("DELETE FROM account_lifecycles WHERE user_id=%s AND lifecycle_id=%s", (UID, OLD)).rowcount != 1:
                    raise ValueError("race lifecycle changed before replacement")
                if conn.execute("DELETE FROM users WHERE id=%s AND username='replacement' AND email='replacement@example.test'", (UID,)).rowcount != 1:
                    raise ValueError("race account changed before replacement")
                conn.execute("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (%s,'race-replacement','race-replacement@example.test','synthetic',true,false)", (UID,))
                conn.execute("INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (%s,%s)", (UID, new_ref))
            with MARKER.open("x", encoding="utf-8") as out:
                json.dump({"old_ref": OLD, "new_ref": new_ref}, out)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(502)
        finally:
            upstream.close()


if __name__ == "__main__":
    try:
        HTTPServer(("127.0.0.1", PORT), Proxy).serve_forever(poll_interval=.1)
    finally:
        conn.close()
