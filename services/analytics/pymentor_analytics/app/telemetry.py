"""Bounded process counters and private operational text metrics; no PII labels."""
import json
import math
import os
import sys
from threading import Lock
import time

from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS

OUTCOMES = ("accepted", "duplicate", "conflict", "rejected", "pending")
REQUESTS = ("ok", "invalid", "unauthorized", "forbidden", "overloaded", "unavailable", "server_error", "other")
MAX_ACK_BYTES = 64 * 1024


def resident_memory_bytes():
    """Own Linux process RSS; unavailable platforms report unknown, never zero."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open("/proc/self/statm", encoding="ascii") as source:
            fields = source.read(1024).split()
        pages, page_bytes = int(fields[1]), os.sysconf("SC_PAGE_SIZE")
        if pages < 0 or page_bytes <= 0:
            return None
        return pages * page_bytes
    except (OSError, ValueError, IndexError):
        return None


class Telemetry:
    def __init__(self):
        self.started = time.monotonic()
        self._lock = Lock()
        self.events = dict.fromkeys(OUTCOMES, 0)
        self.requests = dict.fromkeys(REQUESTS, 0)
        self.instrumentation_errors = 0

    def response(self, status, body):
        request = {200: "ok", 400: "invalid", 401: "unauthorized", 403: "forbidden", 413: "invalid",
                   422: "invalid", 429: "overloaded", 503: "unavailable", 500: "server_error"}.get(status, "other")
        with self._lock:
            self.requests[request] += 1
            if status != 200:
                return
            try:
                value = json.loads(body)
                items = value["items"]
                if not isinstance(items, list) or len(items) > 100:
                    raise ValueError
                counts = dict.fromkeys(OUTCOMES, 0)
                for item in items:
                    counts[item["status"]] += 1
                for key in OUTCOMES:
                    self.events[key] += counts[key]
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                self.instrumentation_errors += 1

    def render(self, role, ready, diagnostics=None, pool=None):
        if role not in {"collector", "processor", "query", "privacy"}:
            raise ValueError("unknown metrics role")
        lines = [f'analytics_privacy_ready{{role="{role}"}} {int(ready is True)}',
                 f'analytics_runtime_uptime_seconds{{role="{role}"}} {max(0,time.monotonic()-self.started):.6f}',
                 f'analytics_process_cpu_seconds_total{{role="{role}"}} {time.process_time():.6f}']
        memory = resident_memory_bytes()
        lines.append(f'analytics_process_resident_memory_bytes_known{{role="{role}"}} {int(memory is not None)}')
        if memory is not None:
            lines.append(f'analytics_process_resident_memory_bytes{{role="{role}"}} {memory}')
        pool_fields = ("size", "checked_in", "checked_out", "max", "overflow")
        for index, key in enumerate(pool_fields):
            value = pool[index] if pool is not None else None
            if type(value) is not int or value < 0 or value > 2**63-1:
                value = None
            lines.append(f'analytics_database_pool_{key}_known{{role="{role}"}} {int(value is not None)}')
            if value is not None:
                lines.append(f'analytics_database_pool_{key}{{role="{role}"}} {value}')
        with self._lock:
            if role == "collector":
                lines.extend(f'analytics_collector_ack_events_total{{outcome="{key}"}} {self.events[key]}' for key in OUTCOMES)
                lines.extend(f'analytics_collector_requests_total{{outcome="{key}"}} {self.requests[key]}' for key in REQUESTS)
                lines.append(f"analytics_instrumentation_errors_total {self.instrumentation_errors}")
        if role == "processor":
            lines.append(f"analytics_diagnostics_available {int(diagnostics is not None)}")
            if diagnostics is not None:
                lines.extend(render_diagnostics(diagnostics))
            else:
                lines.append("analytics_database_waiting_locks_known 0")
        return "\n".join(lines) + "\n"


def render_diagnostics(snapshot):
    def number(value):
        if (type(value) not in {int, float} or value < 0
                or (type(value) is int and value > 2**63-1)
                or (type(value) is float and not math.isfinite(value))):
            raise ValueError("invalid aggregate metric")
        return str(value)
    fields = ("stored_facts", "unknown_consumer_jobs", "unknown_status_jobs", "retrying_jobs", "quarantined_jobs",
              "active_replays", "database_bytes", "database_connections")
    lines = [f"analytics_{key} {number(getattr(snapshot,key))}" for key in fields]
    for key in ("oldest_pending_seconds", "privacy_applied_through", "database_waiting_locks"):
        value = getattr(snapshot, key)
        lines.append(f"analytics_{key}_known {int(value is not None)}")
        if value is not None:
            lines.append(f"analytics_{key} {number(value)}")
    allowed = {(sub.consumer, sub.version) for sub in SYNTHETIC_SUBSCRIPTIONS}
    if len(snapshot.jobs) > 8:
        raise ValueError("unbounded diagnostic labels")
    seen = set()
    for row in snapshot.jobs:
        identity = (row.consumer, row.version, row.status)
        if ((row.consumer, row.version) not in allowed or row.status not in {"pending", "claimed", "completed", "quarantined"}
                or identity in seen):
            raise ValueError("invalid diagnostic label")
        seen.add(identity)
        lines.append(f'analytics_jobs{{consumer="{row.consumer}",version="{row.version}",status="{row.status}"}} {number(row.count)}')
    return lines


class IntakeTelemetryMiddleware:
    def __init__(self, app, *, telemetry):
        self.app, self.telemetry = app, telemetry

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/api/analytics/events/batch" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        status, body, overflow = 500, bytearray(), False
        async def capture(message):
            nonlocal status, overflow
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if status == 200:
                    if len(body) + len(chunk) <= MAX_ACK_BYTES and not overflow:
                        body.extend(chunk)
                    else:
                        body.clear()
                        overflow = True
                if not message.get("more_body", False):
                    self.telemetry.response(status, bytes(body))
            await send(message)
        await self.app(scope, receive, capture)

# CI scope verification fixture.
