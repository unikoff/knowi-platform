"""Local-only CORP-607 delivery fixture and Collector response-loss runner."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import shadow_607_target as target
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.windows_v1 import snapshot_digest
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

PLAN = "corp607-delivery-plan.json"
SCOPE = "corp607-delivery-scope.json"
ARMED = "corp607-delivery-armed.json"
COMMITTED = "corp607-delivery-first-commit.json"
DISCONNECTED = "corp607-delivery-disconnect.json"
SUBJECT = "60700000-0000-4000-8000-000000000003"
WORKTREE = Path("C:/Users/Unik/.codex/worktrees/analytics-windows-v1/Pymentor_platform")
BASE = "399df8548093609eaf168441be8679c8b6d80b37"
SOURCES = (
    "services/core-api/pymentor_core/app/factory.py",
    "services/core-api/pymentor_core/app/analytics_windows_gateway.py",
    "services/core-api/pymentor_core/modules/analytics_delivery/windows_grants.py",
    "services/core-api/pymentor_core/modules/analytics_delivery/signing.py",
    "services/analytics/pymentor_analytics/app/config.py",
    "services/analytics/pymentor_analytics/app/factory.py",
    "services/analytics/pymentor_analytics/contracts/windows_v1.py",
    "services/analytics/pymentor_analytics/modules/collector/api/windows_router.py",
    "services/analytics/pymentor_analytics/adapters/postgres/student_windows.py",
    "services/analytics/pymentor_analytics/adapters/postgres/intake.py",
    "services/analytics/pymentor_analytics/adapters/postgres/privacy.py",
    "services/analytics/pymentor_analytics/app/student_windows_runtime.py",
    "services/analytics/migrations/versions/a015_student_activity_windows.py",
)


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def source_identity():
    if target.REPOSITORY.resolve() != WORKTREE.resolve():
        raise ValueError("run only in the assigned delivery worktree")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=WORKTREE, text=True, timeout=10).strip()
    if head != BASE:
        raise ValueError("delivery source baseline changed")
    files = {file: sha256((WORKTREE / file).read_bytes()).hexdigest() for file in SOURCES}
    return {"head": head, "sha256": sha256(wire(files)).hexdigest(), "components": files}


def read(local, folder, name, maximum=32768):
    return json.loads(local._read(folder, name, maximum), object_pairs_hook=local._unique)


def load_target(instance):
    instance = str(instance)
    data = target.bootstrap.config(target.CONNECTION)
    local, folder, _ = target.runtime._identity(instance)
    guards = read(local, folder, "corp607-pending.json", 4096)
    expected_analytics = "analytics_live_" + UUID(instance).hex
    expected_core = "core_607_" + UUID(instance).hex
    if (guards["owner"] != "607" or guards["instance_id"] != instance
            or guards["analytics"] != expected_analytics or guards["core"] != expected_core
            or guards["analytics_owner"] != expected_analytics + "_migrate"
            or guards["core_owner"] != expected_core + "_owner"
            or read(local, folder, "corp607-schema.json", 4096) != {"analytics_schema": "a015"}):
        raise ValueError("delivery requires a dedicated CORP-607 a015 target")
    details = {}
    for role, expected in (("analytics", "a015"), ("core", "c528_course_leaders")):
        with target.connect_owned(data, guards[role]) as connection:
            observed = target.observe_identity(connection, guards[role], guards[role + "_owner"], guards["sentinel"][role])
            revision, = connection.execute("SELECT version_num FROM public.alembic_version").fetchone()
            if revision != expected:
                raise ValueError(f"unexpected {role} schema revision")
            details[role] = {**observed, "revision": revision}
    learner = read(local, folder, "corp607-learners.json")["lost"]
    if learner["subject_ref"] != SUBJECT or not learner["email"].endswith("@example.test"):
        raise ValueError("delivery requires its synthetic lost fixture")
    return data, local, folder, guards, details, learner


def choose_slots(instant):
    rounded = instant.astimezone(ZoneInfo("Europe/Moscow"))
    rounded = rounded.replace(minute=rounded.minute // 15 * 15, second=0, microsecond=0)
    rows = []
    for offset in range(5, 0, -1):
        start = rounded - timedelta(minutes=15 * offset)
        slot = start.hour * 4 + start.minute // 15 + 1
        if slot != 45:
            rows.append({"activity_date": start.date().isoformat(), "slot_no": slot, "start": start.isoformat()})
    return rows[:4]


def prepare(instance):
    identity = source_identity()
    data, local, folder, guards, _, _ = load_target(instance)
    if local._file(folder, PLAN, required=False).exists():
        raise ValueError("delivery plan already exists; reconcile its evidence before retry")
    with target.connect_owned(data, guards["analytics"]) as pg:
        pg.execute("BEGIN READ ONLY")
        for table, column in (("student_activity_windows", "student_id"),
                              ("student_activity_window_control", "student_id"), ("activity_facts", "subject_ref")):
            if pg.execute(f"SELECT 1 FROM analytics.{table} WHERE {column}=%s LIMIT 1", (SUBJECT,)).fetchone():
                raise ValueError("delivery learner already has data; use a fresh 607 target")
        pg.execute("ROLLBACK")
    slots = choose_slots(datetime.now(timezone.utc))
    if len(slots) != 4:
        raise ValueError("cannot choose four isolated closed windows")
    plan = {
        "version": 1, "instance_id": str(instance), "subject_ref": SUBJECT,
        "writer_a": str(uuid4()), "writer_b": str(uuid4()), "source": identity,
        "windows": [dict(slots[0], active_ms=240000), dict(slots[1], active_ms=360000)],
        "old_v2": [dict(item, event_id=str(uuid4()), active_ms=60000) for item in slots[2:]],
        "t0": datetime.fromisoformat(slots[0]["start"]).astimezone(timezone.utc).isoformat(),
    }
    target.live._write(local, folder, SCOPE, {SUBJECT: {"course_ids": ["backend"], "tool_scopes": []}})
    target.live._write(local, folder, PLAN, plan)
    print(wire({"instance_id": str(instance), "target": guards["analytics"],
                "source_sha256": identity["sha256"], "expected": plan["windows"]}).decode("ascii"))


def checked_plan(instance):
    data, local, folder, guards, details, learner = load_target(instance)
    plan = read(local, folder, PLAN)
    if (plan["version"] != 1 or plan["instance_id"] != str(instance) or plan["subject_ref"] != SUBJECT
            or plan["source"] != source_identity() or len(plan["windows"]) != 2
            or any(row["slot_no"] == 45 for row in plan["windows"] + plan["old_v2"])):
        raise ValueError("unrecognized or changed delivery plan")
    return data, local, folder, guards, details, learner, plan


class FaultResponse:
    """A local test ASGI wrapper. Only one committed, pre-armed key enters it."""
    def __init__(self, app, local, folder, plan):
        self.app, self.local, self.folder, self.plan = app, local, folder, plan
        self.marker = ContextVar("delivery_no_ack", default=None)
        self.wire_digest = ContextVar("delivery_wire_digest", default=None)
        self.used = False

    def hook(self, store):
        original = store.write_window
        key_expected = self.plan["windows"][0]

        async def lose_first(key, snapshot, *, received_at):
            candidate = (
                not self.used and str(key.student_id) == self.plan["subject_ref"]
                and key.activity_date.isoformat() == key_expected["activity_date"]
                and key.slot_no == key_expected["slot_no"] and str(snapshot.writer_id) == self.plan["writer_a"]
                and snapshot.writer_generation == snapshot.server_epoch == snapshot.seq == 1
            )
            if not candidate or not self.local._file(self.folder, ARMED, required=False).exists():
                return await original(key, snapshot, received_at=received_at)
            armed = read(self.local, self.folder, ARMED, 4096)
            received_digest = self.wire_digest.get()
            if (armed.get("instance_id") != self.plan["instance_id"]
                    or armed.get("snapshot_sha256") != snapshot_digest(snapshot)
                    or received_digest is None or armed.get("body_sha256") != received_digest.hexdigest()):
                raise RuntimeError("fault arm does not match delivery snapshot")
            self.used = True
            outcome = await original(key, snapshot, received_at=received_at)
            if outcome.status != "committed" or outcome.state.confirmed is None:
                return outcome
            record = {
                "activity_date": key_expected["activity_date"], "slot_no": key.slot_no,
                "body_sha256": armed["body_sha256"], "snapshot_sha256": armed["snapshot_sha256"],
                "revision": outcome.state.confirmed.revision, "after_commit": True,
            }
            target.live._write(self.local, self.folder, COMMITTED, record)
            self.marker.set(record)
            raise IntakeUnavailable("controlled reply loss after first commit")

        store.write_window = lose_first
        return original

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/api/analytics/windows/batch":
            return await self.app(scope, receive, send)
        setting = self.marker.set(None)
        body_hash = sha256()
        digest_context = self.wire_digest.set(body_hash)
        sent = False

        async def capture_receive():
            message = await receive()
            if message["type"] == "http.request":
                body_hash.update(message.get("body", b""))
            return message

        async def guarded_send(message):
            nonlocal sent
            record = self.marker.get()
            if record is None:
                return await send(message)
            if sent:
                return
            sent = True
            disconnected = False
            try:
                async with asyncio.timeout(12):
                    while not disconnected:
                        disconnected = (await receive())["type"] == "http.disconnect"
            except TimeoutError:
                pass
            target.live._write(self.local, self.folder, DISCONNECTED,
                               {"body_sha256": record["body_sha256"], "peer_disconnected": disconnected})

        try:
            await self.app(scope, capture_receive, guarded_send)
        finally:
            self.wire_digest.reset(digest_context)
            self.marker.reset(setting)


def with_fault(app, local, folder, plan):
    wrapped = FaultResponse(app, local, folder, plan)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            store = application.state.windows_store
            if store is None or application.state.database.transaction_guard is None:
                raise RuntimeError("fault case requires real window storage and Privacy")
            original = wrapped.hook(store)
            try:
                yield
            finally:
                store.write_window = original

    app.router.lifespan_context = lifespan
    return wrapped


def serve_analytics(instance):
    _, local, folder, _, _, _, plan = checked_plan(instance)
    from pymentor_analytics.app import factory

    if local._file(folder, COMMITTED, required=False).exists():
        raise ValueError("fault already ran; reconcile its evidence")
    _, _, _, configurations = target.runtime.read_settings(str(instance))
    collector = configurations["collector"]
    configurations["collector"] = Settings.model_validate({
        **collector.model_dump(), "windows_intake_enabled": True, "windows_reconciliation_enabled": False,
        "windows_cohort": [SUBJECT], "windows_t0": plan["t0"],
    })
    original = factory.create_app

    def build(role, settings):
        app = original(role, settings)
        return with_fault(app, local, folder, plan) if role == "collector" else app

    factory.create_app = build
    try:
        asyncio.run(target.runtime._serve_all(str(instance), local, folder, configurations))
    finally:
        factory.create_app = original


def core_recipe(instance, local, folder, plan):
    original = read(local, folder, "corp607-core-runtime.json")
    settings = {**target.base_env(), **original, "ANALYTICS_WINDOWS_INTAKE_ENABLED": "1",
                "ANALYTICS_WINDOWS_RECONCILIATION_ENABLED": "0", "ANALYTICS_WINDOWS_COHORT": SUBJECT,
                "ANALYTICS_WINDOWS_T0": plan["t0"],
                "ANALYTICS_WINDOWS_SCOPE_FILE": str(local._file(folder, SCOPE)),
                "ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN": "http://127.0.0.1:18042"}
    command = [str(target.CORE_PYTHON), "-B", "-m", "uvicorn", "pymentor_core.app.asgi:create_app",
               "--factory", "--host", "127.0.0.1", "--port", "18041", "--no-access-log"]
    return command, settings


def serve_core(instance):
    _, local, folder, _, _, _, plan = checked_plan(instance)
    command, settings = core_recipe(instance, local, folder, plan)
    with (folder / "delivery-core.log").open("xb") as log:
        process = subprocess.Popen(command, cwd=target.CORE, env=settings, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(wire({"instance_id": str(instance), "core_pid": process.pid, "source_sha256": plan["source"]["sha256"]}).decode("ascii"), flush=True)
        try:
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "serve-core", "serve-analytics"))
    parser.add_argument("--instance-id", type=UUID, required=True)
    options = parser.parse_args()
    actions = {"plan": prepare, "serve-core": serve_core, "serve-analytics": serve_analytics}
    return actions[options.action](options.instance_id)


if __name__ == "__main__":
    raise SystemExit(main())
