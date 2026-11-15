"""Measure real local ASGI/PG/Redis scheduling on synthetic, run-owned data.

No DB schema change or real notification delivery. Credentials stay in memory.
This captures actual driver/thread callsites, not an estimate from route syntax.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import secrets
import threading
import time
import traceback
from unittest.mock import patch
from uuid import uuid4

from multicourse_local import ROOT, configure, DEFAULT_RUN


def process_memory():
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    process = kernel.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return None
    return {"rss_bytes": counters.working_set, "peak_rss_bytes": counters.peak_working_set}


def percentiles(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {"samples": len(ordered), "p50_ms": ordered[math.ceil(len(ordered) * .50) - 1], "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1], "max_ms": ordered[-1]}


def seed():
    from pymentor_core.app.database import SessionLocal, engine
    from pymentor_core.adapters.sql.models import User
    from sqlalchemy import text

    if engine.url.host != "127.0.0.1" or engine.url.port != 57152 or engine.url.database != "pymentor_test" or engine.url.username != "pymentor_app":
        raise RuntimeError("only the owned synthetic lab is permitted")
    password = secrets.token_urlsafe(24)
    email = "mc195-" + uuid4().hex + "@example.invalid"
    with SessionLocal() as session:
        if tuple(session.execute(text("SELECT current_database(), current_user")).one()) != ("pymentor_test", "pymentor_app"):
            raise RuntimeError("database identity mismatch")
        user = User(username="MC195 synthetic", email=email, verified=True, is_admin=False, subscription_until=datetime.now(timezone.utc) + timedelta(days=30), subscription_plan="audit")
        user.password = password
        session.add(user)
        session.commit()
    return email, password


async def measure(email, password):
    import httpx
    from sqlalchemy import event
    from pymentor_core.app.asgi import create_app
    from pymentor_core.app.database import engine
    from pymentor_core.adapters.course_local.runtime import default_runtime

    loop_thread = threading.get_ident()
    events = []
    group_name = ContextVar("audit_request_group", default="lifespan")

    def capture(driver):
        def before(connection, cursor, statement, parameters, context, executemany):
            stack = []
            for frame in traceback.extract_stack(limit=40):
                path = Path(frame.filename)
                if path.is_relative_to(ROOT / "services/core-api/pymentor_core"):
                    stack.append({"file": path.relative_to(ROOT).as_posix(), "line": frame.lineno, "function": frame.name})
            events.append({"group": group_name.get(), "driver": driver, "on_event_loop_thread": threading.get_ident() == loop_thread, "operation": statement.split(None, 1)[0].upper() if statement.strip() else "EMPTY", "stack": stack[-8:]})
        return before

    sync_listener = capture("psycopg-sync")
    async_listener = capture("asyncpg")
    event.listen(engine, "before_cursor_execute", sync_listener)
    app = create_app()
    groups = []
    started = time.perf_counter()
    try:
        async with app.router.lifespan_context(app):
            startup_ms = (time.perf_counter() - started) * 1000
            startup_memory = process_memory()
            async_engine = app.state.local_async_identity.database.engine.sync_engine
            event.listen(async_engine, "before_cursor_execute", async_listener)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost") as client:
                async def group(name, requests):
                    group_token = group_name.set(name)
                    lag = []
                    stop = False

                    async def heartbeat():
                        expected = time.perf_counter() + .005
                        while not stop:
                            await asyncio.sleep(.005)
                            now = time.perf_counter()
                            lag.append(max(0., (now - expected) * 1000))
                            expected = now + .005

                    async def request(method, url, body=None, headers=None):
                        begin = time.perf_counter()
                        response = await client.request(method, url, json=body, headers=headers)
                        result = {"method": method, "path": url.split("?", 1)[0], "status": response.status_code, "latency_ms": (time.perf_counter() - begin) * 1000}
                        if response.status_code == 200 and response.headers.get("content-type", "").startswith("application/json"):
                            body = response.json()
                            for flag in ("ok", "completed"):
                                if type(body.get(flag)) is bool:
                                    result[flag] = body[flag]
                        return result

                    ticker = asyncio.create_task(heartbeat())
                    await asyncio.sleep(.010)
                    try:
                        rows = await asyncio.gather(*(request(*args) for args in requests))
                        await asyncio.sleep(.010)
                    finally:
                        stop = True
                        await ticker
                        group_name.reset(group_token)
                    result = {"name": name, "concurrency": len(requests), "statuses": dict(Counter(str(row["status"]) for row in rows)), "latency": percentiles([row["latency_ms"] for row in rows]), "loop_lag": percentiles(lag), "requests": rows}
                    groups.append(result)
                    print(json.dumps({k: v for k, v in result.items() if k != "requests"}), flush=True)
                    return rows

                await group("cold_catalog", [("GET", "/api/learning/catalog")])
                await group("login", [("POST", "/api/user/login", {"email": email, "password": password}, {"Idempotency-Key": str(uuid4())})])
                if not client.cookies.get("session"):
                    raise RuntimeError("synthetic login did not establish a session")
                await group("authenticated_reads", [("GET", ["/api/learning/catalog", "/api/user/me", "/api/user/activity?year=2026&month=11"][i % 3]) for i in range(15)])
                snapshot = default_runtime().snapshot("backend")
                manual = next(lesson for lesson in snapshot.catalog.LESSONS if lesson["self_check"] and not lesson["tasks"] and not lesson["source_file"].startswith("chapter_01/"))
                task = next(task for lesson in snapshot.catalog.LESSONS for task in lesson["tasks"])
                await group("concurrent_completion", [("POST", f'/api/learning/lessons/{manual["id"]}/complete') for _ in range(6)])
                await group("rejected_submit", [("POST", f'/api/learning/tasks/{task["id"]}/submit', {"code": 'raise ValueError("synthetic rejected answer")', "task_revision": task["revision"]}) for _ in range(3)])
                await group("interpreter", [("POST", "/api/learning/interpreter/run", {"code": "print(1 + 1)"}) for _ in range(6)])
            event.remove(async_engine, "before_cursor_execute", async_listener)
    finally:
        event.remove(engine, "before_cursor_execute", sync_listener)
    calls = Counter((row["group"], row["driver"], row["on_event_loop_thread"]) for row in events)
    return {"mode": "actual in-process ASGI + owned PostgreSQL/Redis; synthetic user; no external delivery", "quantile_method": "nearest rank", "heartbeat_interval_ms": 5, "startup_ms": startup_ms, "startup_memory": startup_memory, "end_memory": process_memory(), "tracing_overhead": "SQL call stack collection enabled; not a capacity benchmark", "groups": groups, "sql_summary": [{"group": key[0], "driver": key[1], "on_event_loop_thread": key[2], "calls": count} for key, count in calls.items()], "sql_callsites": events}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-no-warmup", action="store_true", help="diagnostic controlled baseline: defer course snapshots until first request")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("choose a new evidence path; existing reports are never overwritten")
    configure(DEFAULT_RUN)
    email, password = seed()
    if args.baseline_no_warmup:
        async def deferred():
            pass
        with patch("pymentor_core.app.wiring.warm_course_snapshots", deferred):
            report = asyncio.run(measure(email, password))
    else:
        report = asyncio.run(measure(email, password))
    report["course_warmup"] = not args.baseline_no_warmup
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(args.output), "sql_summary": report["sql_summary"]}))


if __name__ == "__main__":
    main()
