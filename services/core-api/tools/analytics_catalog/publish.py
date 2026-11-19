"""Explicit publication command; synthetic/local use only until release gates pass."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import hmac
import importlib.util
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymentor_core.adapters.analytics_catalog.publication import canonical, clear_pending, current_digest, digest, project, publication_lock, read_receipt, write_receipt
from pymentor_core.adapters.analytics_http.http import request
from pymentor_core.adapters.course_local.runtime import CourseRuntime
from pymentor_core.modules.analytics_delivery.signing import Signer
from pymentor_core.adapters.analytics_catalog.fixture_guard import fixture_root, read_json


def checked_fixture(folder_value, courses_root, course_id, endpoint, owner_token, *, verify_databases=None):
    """Validate exact owned fixture and both database sentinels before network I/O."""
    folder = fixture_root(folder_value)
    owner = read_json(folder / "owner.json")
    token = owner.get("token")
    if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
            or not isinstance(owner_token, str) or not hmac.compare_digest(token, owner_token)
            or owner.get("state") not in {"processes_started", "http_acceptance_running", "browser_hold"}):
        raise ValueError("fixture ownership is not active")
    courses = folder / "courses"
    if courses.is_symlink() or os.path.isjunction(courses) or courses.resolve(strict=True).parent != folder:
        raise ValueError("invalid fixture course directory")
    if Path(courses_root).resolve(strict=True) != courses.resolve(strict=True):
        raise ValueError("course registry is outside fixture")
    fixture = read_json(folder / "fixture.json")
    # Reuse the cross-service fixture's exact URL, user and DB shape contract.
    guard_file = Path(__file__).resolve().parents[3] / "analytics/tests/cross_service/guards.py"
    spec = importlib.util.spec_from_file_location("catalog_cross_service_guards", guard_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate(fixture)
    if fixture["course_id"] != course_id or fixture["collector_url"].rstrip("/") != endpoint.rstrip("/"):
        raise ValueError("fixture course or collector mismatch")
    core, analytics = fixture["databases"]["core"], fixture["databases"]["analytics"]
    owned_analytics = owner.get("analytics_resources")
    if (owner.get("core_name") != core["name"] or owner.get("core_sentinel") != core["sentinel"]
            or not isinstance(owned_analytics, list)
            or sum(item == {"name": analytics["name"], "sentinel": analytics["sentinel"]} for item in owned_analytics) != 1):
        raise ValueError("fixture database ownership mismatch")
    (verify_databases or module.verify_databases)(fixture)
    return folder


async def publish(snapshot, endpoint, token, *, transport=None):
    with publication_lock(snapshot.context.course_id):
        return await _publish_locked(snapshot, endpoint, token, transport=transport)


async def _publish_locked(snapshot, endpoint, token, *, transport=None):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("local HTTP endpoint required")
    course = snapshot.context.course_id
    try:
        pending = read_receipt(course, pending=True)
    except FileNotFoundError:
        pending = None
    if pending:
        message, source_digest = pending["message"], pending["source_digest"]
    else:
        try:
            prior = read_receipt(course)["message"]
        except FileNotFoundError:
            prior = None
        message, source_digest = project(snapshot, prior)
    fingerprint = digest(message)
    path = "/internal/analytics/catalog/v1/receipts/" + quote(course, safe="") + "/" + quote(message["catalog_version"], safe="") + "?" + urlencode({"fingerprint": fingerprint})
    headers = {"Authorization": "Bearer " + token(), "Accept": "application/json", "Content-Type": "application/json"}

    async def call(method, route, body=None):
        result = await request(method, endpoint.rstrip("/") + route, headers=headers, body=body, deadline_seconds=5, max_bytes=4096, transport=transport)
        if result is None:
            return "unknown"
        code, raw = result
        try:
            value = json.loads(raw)
            if code not in (200, 409) or set(value) != {"status"}:
                return "unknown"
            return value["status"] if value["status"] in {"accepted", "absent", "conflict"} else "unknown"
        except (ValueError, TypeError):
            return "unknown"

    # Persist the exact candidate before sending, so a lost ACK remains recoverable
    # even when source files change before the next invocation.
    if pending is None:
        write_receipt(course, {"message": message, "fingerprint": fingerprint, "source_digest": source_digest}, pending=True)
    # Always reconcile first, including a previous lost response.
    status = await call("GET", path)
    if status == "absent":
        status = await call("POST", "/internal/analytics/catalog/v1", canonical({"message": message, "fingerprint": fingerprint}))
        # A POST response alone is not the publication proof.
        if status in {"accepted", "unknown"}:
            status = await call("GET", path)
    if status != "accepted":
        return status
    write_receipt(course, {"message": message, "fingerprint": fingerprint, "source_digest": source_digest})
    clear_pending(course)
    if current_digest(snapshot) != source_digest:
        return "changed"
    return "accepted"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--courses-root", required=True, help="owned fixture course registry")
    parser.add_argument("--course-id", required=True)
    args = parser.parse_args()
    root = Path(args.courses_root).resolve(strict=True)
    env = os.environ
    checked_fixture(env.get("PYMENTOR_ANALYTICS_CATALOG_FIXTURE_DIR"), root, args.course_id,
                    env["PYMENTOR_ANALYTICS_CATALOG_URL"], env.get("CROSS_SERVICE_OWNER_TOKEN"))
    signer = Signer(issuer=env["PYMENTOR_ANALYTICS_PRODUCER_ISSUER"], kid=env["PYMENTOR_ANALYTICS_PRODUCER_KID"],
                    private_pem=Path(env["PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE"]).read_bytes(), purpose="producer")
    snapshot = CourseRuntime(root).snapshot(args.course_id)
    result = asyncio.run(publish(snapshot, env["PYMENTOR_ANALYTICS_CATALOG_URL"], lambda: signer.producer(env["PYMENTOR_ANALYTICS_PRODUCER_ID"])))
    print(result)
    if result != "accepted":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
