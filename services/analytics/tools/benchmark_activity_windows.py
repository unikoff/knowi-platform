"""CORP-607 paired, opt-in HTTP load over separately owned disposable targets."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
import hashlib
import json
import math
import os
import sys
from pathlib import Path
import time
from uuid import UUID, uuid5

import httpx
import benchmark_activity as base
from benchmark_windows_target import CONNECTION_COUNT_SCOPE, Target, guarded_pg, phase_observer, private_manifest
from pymentor_analytics.contracts.reports_v1 import ActivityReport
from pymentor_analytics.contracts.v2 import AcceptedEvent, BatchAck, BrowserEvent
from pymentor_analytics.contracts.windows_v1 import (
    WindowBatchAck, WindowGrantResponse, WindowSnapshot, can_remove_confirmed_snapshot,
)
from reference.activity_v1 import calculate as activity_oracle

MSK = timezone(timedelta(hours=3))
MINUTE = timedelta(minutes=1)
REPORT_PATH = "/api/analytics/reports/activity"
WINDOW_PATH = "/api/analytics/windows"
V2_PATH = "/api/analytics/events/batch"
MODES = {"smoke": (60, 120), "full": (300, 900)}


@dataclass(frozen=True)
class Course:
    course_id: str
    catalog_version: str
    lesson_id: str


class Plan:
    def __init__(self, profile, stage, run_id, anchor, mode, courses):
        if anchor.utcoffset() != timedelta(0) or anchor.second or anchor.microsecond or anchor.minute % 15:
            raise ValueError("align anchor to a UTC quarter hour")
        if mode == "smoke" and stage != 50:
            raise ValueError("607.A smoke uses the 50-learner profile")
        stream_plan = base.stream_plan(profile, stage, profile["seed"], run_id)
        self.seed = profile["seed"]
        self.stage, self.run_id, self.anchor = stage, run_id, anchor
        self.courses = tuple(courses)
        self.users, self.active, self.overlap = (
            tuple(UUID(int=subject.int, version=4) for subject in stream_plan["subjects"]),
            stream_plan["active"], stream_plan["overlapping"]
        )
        self.warmup, self.duration = MODES[mode]
        self.minutes = (self.warmup + self.duration) // 60
        self.measure_start = anchor + timedelta(seconds=self.warmup)
        self.end = anchor + self.minutes * MINUTE

    def writer(self, subject):
        return uuid5(self.run_id, f"windows:{subject}")

    def course_id(self, position):
        return self.courses[position].course_id

    @staticmethod
    def section(minute):
        return "theory" if minute % 2 == 0 else "practice"

    def v2(self, minute):
        """One immutable set of input events is materialized before either phase."""
        for index, subject in enumerate(self.users[:self.active]):
            for device in range(2 if index < self.overlap else 1):
                course = self.courses[device]
                stream = {"subject": subject, "user": index, "device": device, "course": course.course_id}
                start = self.anchor + minute * MINUTE
                event = base.event_for(
                    stream, run_id=self.run_id, seed=self.seed, stage=self.stage, number=minute,
                    start=start, kind="course_opened" if minute < 0 else "activity_segment",
                    cadence_seconds=60, section=self.section(minute),
                )
                if minute < 0:
                    event.update(started_at=self.anchor.isoformat(), ended_at=self.anchor.isoformat(),
                                 last_action_at=self.anchor.isoformat())
                else:
                    event["lesson_id"] = course.lesson_id
                    if device == 1:
                        later = (start + timedelta(seconds=30)).isoformat()
                        event.update(started_at=later, last_action_at=later, active_ms=30_000)
                event["catalog_version"] = course.catalog_version
                yield subject, BrowserEvent.model_validate_json(json.dumps(event))

    def snapshots(self, minutes):
        """Latest-start interval winner supplies the one focused context per instant."""
        accumulated = defaultdict(Counter)
        for minute, inputs in enumerate(minutes):
            slot = (self.anchor + minute * MINUTE).astimezone(MSK)
            coordinates = slot.date().isoformat(), 1 + (slot.hour * 60 + slot.minute) // 15
            by_subject = defaultdict(list)
            for subject, event in inputs:
                by_subject[subject].append(event)
            for subject, events in by_subject.items():
                key = subject, *coordinates
                points = sorted({point for event in events for point in (event.started_at, event.ended_at)})
                for start, end in zip(points, points[1:]):
                    selected = max((e for e in events if e.started_at <= start and e.ended_at >= end),
                                   key=lambda e: e.started_at)
                    context = selected.section, selected.course_id, selected.lesson_id
                    accumulated[key][context] += (end - start) // timedelta(milliseconds=1)
                stats = [dict(tool_id=section, course_id=course, lesson_id=lesson, active_ms=amount)
                         for (section, course, lesson), amount in sorted(accumulated[key].items())]
                versions = {course.course_id: course.catalog_version for course in self.courses
                            if any(row["course_id"] == course.course_id for row in stats)}
                raw = dict(activity_date=coordinates[0], slot_no=coordinates[1], writer_id=str(self.writer(subject)),
                           writer_generation=1, server_epoch=1, seq=1 + slot.minute % 15,
                           client_final=slot.minute % 15 == 14, stats=stats, catalog_versions=versions)
                yield minute, subject, WindowSnapshot.model_validate_json(json.dumps(raw))

    def expected_windows(self):
        """Independent fixed arithmetic for the primary and optional overlapping half-minute."""
        result = defaultdict(Counter)
        for index, subject in enumerate(self.users[:self.active]):
            for minute in range(self.minutes):
                time_msk = (self.anchor + minute * MINUTE).astimezone(MSK)
                slot_no = 1 + time_msk.hour * 4 + time_msk.minute // 15
                stats = result[(subject, time_msk.date(), slot_no)]
                section = "theory" if minute % 2 == 0 else "practice"
                for position, amount in enumerate((30_000, 30_000) if index < self.overlap else (60_000,)):
                    course = self.courses[position]
                    stats[(section, course.course_id, course.lesson_id)] += amount
        return {key: sorted((*context, value) for context, value in stats.items()) for key, stats in result.items()}


def work_hash(inputs):
    data = [(str(subject), event.model_dump(mode="json")) for subject, event in inputs]
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def match_rows(expected, actual, receipts, shadow):
    if shadow and (not expected or set(receipts) != set(expected)):
        raise base.ObservedViolation("nonempty independent window denominator or commit receipts missing")
    if set(actual) != (set(expected) if shadow else set()):
        raise base.ObservedViolation("PostgreSQL window key set disagrees with variant")
    for key, (stats, revision) in actual.items():
        if stats != expected[key] or revision != receipts[key]:
            raise base.ObservedViolation("PostgreSQL statistics or revision disagree with oracle/commit ACK")


def totals(report):
    if report.status not in {"ok", "no_data"} or report.totals is None:
        raise base.ObservedViolation("report has no observed values")
    values = report.totals.model_dump()
    if report.as_of is None and any(values.values()):
        raise base.ObservedViolation("nonempty report has no snapshot timestamp")
    return values


def report_expected(events, start, end, as_of):
    expected = activity_oracle(events, start=start, end=end, as_of=as_of, input_complete=False)
    return dict(unique_visitors=expected["visitors"], active_ms=expected["total_ms"],
                theory_ms=expected["theory_ms"], practice_ms=expected["practice_ms"])


class Phase:
    def __init__(self, target, plan, evidence):
        self.target, self.plan, self.evidence = target, plan, evidence
        self.sessions, self.grants, self.receipts = {}, {}, {}
        self.collector_ms, self.query_ms, self.commit_ms, self.lag = [], [], [], []
        self.requests = Counter()
        self.evidence["requests"] = self.requests
        self.pending = 0
        self.measured_existing_key_commits = 0
        self.peak_pending = 0
        self.peak_combined_backlog = 0
        self.admin_backlog = []
        self.admin_id = uuid5(plan.run_id, "admin")
        self.leader_ids = [uuid5(plan.run_id, f"leader:{i}") for i in range(2)]

    async def request(self, method, role, path, *, kind, phase, **kwargs):
        started = time.monotonic()
        try:
            response = await self.client.request(method, self.target.url(role) + path, **kwargs)
        except httpx.HTTPError as error:
            if method == "POST" and kind in {"v2", "v3"}:
                document = kwargs["json"]
                if kind == "v2":
                    self.evidence["unknown_write"] = {"event_id": document["events"][0]["event_id"]}
                else:
                    wire = document["windows"][0]
                    self.evidence["unknown_write"] = {key: wire[key] for key in
                                                       ("activity_date", "slot_no", "writer_id", "writer_generation",
                                                        "server_epoch", "seq")}
            self.requests[f"{phase}:{kind}:{'timeout' if isinstance(error, httpx.TimeoutException) else 'transport'}"] += 1
            reason = ("mutation outcome unknown; reconcile before retry" if method == "POST" and kind in {"v2", "v3"}
                      else "HTTP transport or response timed out")
            raise base.ObservedViolation(reason) from None
        self.requests[f"{phase}:{kind}:{response.status_code}"] += 1
        if response.status_code != 200:
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "")
                self.evidence.setdefault("retry_after", []).append(int(retry_after) if retry_after.isdecimal() else None)
            raise base.ObservedViolation(f"{kind} returned HTTP {response.status_code}")
        return response, (time.monotonic() - started) * 1000

    async def login(self, learners):
        # Carrier does not keep any shared cookie jar authentication between subjects.
        async with httpx.AsyncClient(timeout=5, trust_env=False) as login:
            for subject in self.plan.users[:self.plan.active]:
                login.cookies.clear()
                response = await login.post(self.target.url("core") + "/api/user/login",
                                            json=learners[str(subject)], headers={"Idempotency-Key": str(subject)})
                session = response.cookies.get("session")
                if response.status_code != 200 or not session:
                    raise base.StopRun("Core synthetic session unavailable")
                self.sessions[subject] = "session=" + session

    def signed(self, subject, *, role, scope, courses=(), subjects=(), global_read=False):
        token = base.grant(self.target.key, issuer=self.target.issuer, kid=self.target.kid, subject=subject,
                           role=role, scope=scope, courses=courses, subjects=subjects, global_read=global_read)
        return {"Authorization": "Bearer " + token}

    async def send_v2(self, subject, event, phase):
        authorization = self.signed(subject, role="user", scope="collect", courses=(event.course_id,))
        try:
            response, elapsed = await self.request("POST", "collector", V2_PATH, kind="v2", phase=phase,
                                                   headers=authorization, json={"events": [event.model_dump(mode="json")]})
            ack, = BatchAck.model_validate_json(response.content).items
            if ack.event_id != event.event_id or ack.status != "accepted":
                raise ValueError
        except ValueError:
            raise base.ObservedViolation("v2 did not confirm the exact event") from None
        if phase == "measure":
            self.collector_ms.append(elapsed)
        return time.monotonic()

    async def grant(self, subject, phase):
        current = self.grants.get(subject)
        if current and current.expires_at > datetime.now(UTC) + timedelta(seconds=10):
            return current.grant
        writer = self.plan.writer(subject)
        response, _ = await self.request("POST", "core", WINDOW_PATH + "/grant", kind="grant", phase=phase,
                                         headers={"Cookie": self.sessions[subject]},
                                         json=dict(protocol_version=1, writer_id=str(writer), writer_generation=1))
        try:
            grant = WindowGrantResponse.model_validate_json(response.content)
            if (grant.subject_ref, grant.writer_id, grant.writer_generation) != (subject, writer, 1):
                raise ValueError
        except ValueError:
            raise base.ObservedViolation("grant has incorrect lifecycle or writer") from None
        self.grants[subject] = grant
        return grant.grant

    async def send_v3(self, subject, snapshot, phase):
        token = await self.grant(subject, phase)
        response, elapsed = await self.request(
            "POST", "core", WINDOW_PATH + "/batch", kind="v3", phase=phase,
            headers={"Authorization": "Bearer " + token, "Cookie": self.sessions[subject]},
            json={"protocol_version": 1, "windows": [snapshot.model_dump(mode="json")]},
        )
        try:
            item, = WindowBatchAck.model_validate_json(response.content).items
            if (item.status != "committed" or not can_remove_confirmed_snapshot(
                    snapshot, item, owner=subject, authenticated_subject=subject)
                    or item.confirmed.stats != snapshot.stats):
                raise ValueError
        except ValueError:
            raise base.ObservedViolation("missing exact committed v3 receipt") from None
        key = subject, snapshot.activity_date, snapshot.slot_no
        revision = item.confirmed.revision
        earlier = self.receipts.get(key, 0)
        if revision <= earlier:
            raise base.ObservedViolation("revision did not advance for changed content")
        self.receipts[key] = revision
        self.pending -= 1
        if phase == "measure":
            self.commit_ms.append(elapsed)
            self.measured_existing_key_commits += int(earlier > 0)
        return key, revision

    async def report(self, role, start, end, phase, index=0):
        courses = tuple(course.course_id for course in self.plan.courses)
        params = {"from_time": start.isoformat(), "to_time": end.isoformat()}
        if role == "admin":
            authorization = self.signed(self.admin_id, role=role, scope="report:read", global_read=True)
        elif role == "leader":
            params["course_id"] = courses[index]
            authorization = self.signed(self.leader_ids[index], role=role, scope="report:read",
                                        courses=(courses[index],), subjects=self.plan.users[:self.plan.active])
        else:
            authorization = self.signed(self.plan.users[index], role=role, scope="report:read", courses=courses)
        response, elapsed = await self.request("GET", "query", REPORT_PATH, kind="query", phase=phase,
                                               params=params, headers=authorization)
        try:
            result = ActivityReport.model_validate_json(response.content)
            totals(result)
            if (result.from_time, result.to_time, result.course_id) != (start, end, params.get("course_id")):
                raise ValueError
        except ValueError:
            raise base.ObservedViolation("unexpected v2 report scope or schema") from None
        if phase == "measure":
            self.query_ms.append(elapsed)
        return result

    def observe_admin(self, report, seconds, *, observed_at=None):
        observed_at = time.monotonic() if observed_at is None else observed_at
        actual = totals(report)
        # A successful HTTP query alone cannot prove that the just-ACKed data is visible.
        while self.admin_backlog:
            received, expected_ms = self.admin_backlog[0]
            lag = observed_at - received
            if lag > 60:
                raise base.ObservedViolation("accepted v2 facts did not reach the report within 60 seconds")
            if actual["active_ms"] < expected_ms:
                break
            self.admin_backlog.pop(0)
            self.lag.append(lag)
        if actual["active_ms"] > self.plan.active * seconds * 1000:
            raise base.ObservedViolation("v2 report exceeds independent scheduled activity")

    async def workload(self, minutes, snapshots, openings, observer):
        # The observer takes resource samples on the same ticks in both phases.
        async with httpx.AsyncClient(timeout=5, trust_env=False, limits=httpx.Limits(max_connections=64)) as client:
            self.client = client
            await observer.check(self.client)
            await self.login(self.target.learners)
            if not self.target.shadow:
                subject = self.plan.users[0]
                response = await client.post(
                    self.target.url("core") + WINDOW_PATH + "/grant",
                    headers={"Cookie": self.sessions[subject]},
                    json=dict(protocol_version=1, writer_id=str(self.plan.writer(subject)), writer_generation=1),
                )
                self.evidence["baseline_v3_grant_http"] = response.status_code
                if response.status_code != 404:
                    raise base.ObservedViolation("v2-only Core unexpectedly exposed the v3 grant route")
            for subject, event in openings:
                await self.send_v2(subject, event, "opening")
            wall = datetime.now(UTC)
            clock = time.monotonic()
            measured = None
            for tick in range(1, self.plan.minutes * 4 + 1):
                elapsed = tick * 15
                await asyncio.sleep(max(0, clock + elapsed - time.monotonic()))
                if time.monotonic() - clock - elapsed >= 15:
                    raise base.StopRun("scheduled heartbeat missed")
                if abs((datetime.now(UTC) - wall).total_seconds() - (time.monotonic() - clock)) > 2:
                    raise base.StopRun("wall clock changed during the comparison")
                await observer.check(self.client)
                phase = "measure" if elapsed > self.plan.warmup else "warmup"
                self.evidence["elapsed_seconds"] = time.monotonic() - clock
                start = self.plan.measure_start if phase == "measure" else self.plan.anchor
                end = self.plan.anchor + timedelta(seconds=elapsed)
                if tick % 4 == 0:
                    minute = tick // 4 - 1
                    first_ack = None
                    for subject, event in minutes[minute]:
                        acknowledged = await self.send_v2(subject, event, phase)
                        if first_ack is None:
                            first_ack = acknowledged
                    if phase == "measure":
                        self.admin_backlog.append((first_ack, self.plan.active * (elapsed - self.plan.warmup) * 1000))
                    if self.target.shadow:
                        self.pending = len(snapshots[minute])
                        if phase == "measure":
                            self.peak_pending = max(self.peak_pending, self.pending)
                            self.peak_combined_backlog = max(
                                self.peak_combined_backlog, self.pending + observer.v3_queue())
                        for subject, snapshot in snapshots[minute]:
                            key, revision = await self.send_v3(subject, snapshot, phase)
                            if minute == 0 and not self.evidence.get("first_pg_commit"):
                                await observer.verify_commit(key, snapshot, revision)
                                self.evidence["first_pg_commit"] = True
                if tick % 2 == 0:
                    requests = [self.report("leader", start, end, phase, i) for i in range(2)]
                    requests.append(self.report("admin", start, end, phase))
                    if tick % 4 == 0:
                        requests.extend(self.report("user", start, end, phase, i) for i in range(self.plan.active))
                    reports = await asyncio.gather(*requests, return_exceptions=True)
                    error = next((report for report in reports if isinstance(report, BaseException)), None)
                    if error is not None:
                        raise error
                    if phase == "measure":
                        self.observe_admin(reports[2], elapsed - self.plan.warmup)
                if elapsed == self.plan.warmup:
                    measured = time.monotonic()
                    await observer.boundary("start")
                elif elapsed > self.plan.warmup and tick < self.plan.minutes * 4:
                    await observer.sample()
            if measured is None:
                raise base.StopRun("measurement start missing")
            await observer.boundary("end")
            seconds = time.monotonic() - measured
            self.evidence["measurement_seconds_observed"] = seconds
            if abs(seconds - self.plan.duration) >= 15:
                raise base.StopRun("measured interval differs from announced duration by a heartbeat")
            drain_start = time.monotonic()
            while True:
                await observer.check(self.client)
                report = await self.report("admin", self.plan.measure_start, self.plan.end, "verify")
                self.observe_admin(report, self.plan.duration)
                if not self.admin_backlog and not self.pending and await observer.drained():
                    break
                if time.monotonic() - drain_start >= 60:
                    raise base.ObservedViolation("queue or report did not drain in 60 seconds")
                await asyncio.sleep(1)
            self.evidence["drain_seconds"] = time.monotonic() - drain_start
            return report

    def finish(self, report, facts, expected, actual):
        if report.as_of is None:
            raise base.ObservedViolation("final report has no observed timestamp")
        wanted = report_expected(facts, self.plan.measure_start, self.plan.end, report.as_of)
        if wanted["active_ms"] != self.plan.active * self.plan.duration * 1000 or totals(report) != wanted:
            raise base.ObservedViolation("v2 report disagrees with nonempty independent activity oracle")
        match_rows(expected, actual, self.receipts, self.target.shadow)
        expected_collector = (self.plan.active + self.plan.overlap) * (self.plan.duration // 60)
        expected_query = 3 * (self.plan.duration // 30) + self.plan.active * (self.plan.duration // 60)
        expected_v3 = self.plan.active * (self.plan.duration // 60) if self.target.shadow else 0
        if (len(self.collector_ms), len(self.query_ms), len(self.commit_ms), len(self.lag)) != (
            expected_collector, expected_query, expected_v3, self.plan.duration // 60
        ):
            raise base.ObservedViolation("incomplete request, commit or lag observations")
        self.evidence.update(
            collector=base.summary(self.collector_ms), query=base.summary(self.query_ms),
            v3_core_to_commit_ack=base.summary(self.commit_ms), live_lag_seconds=max(self.lag),
            v3_client_backlog=self.peak_pending, v3_combined_backlog_at_enqueue=self.peak_combined_backlog,
            committed_window_keys_total=len(self.receipts),
            measured_existing_key_commits=self.measured_existing_key_commits,
            report_oracle={"status": "PASS", "totals": wanted, "quality": report.quality},
            pg_window_oracle={"status": "PASS", "denominator": len(expected), "observed": len(actual)},
        )


def assess(phases):
    limits = {"collector_p95_ms": (300, 50), "query_p95_ms": (500, 75),
              "live_lag_seconds": (60, 15), "cpu_cores": (4, 1.5), "rss_mib": (2048, None),
              "connections": (24, None), "pool_overflow": (0, None), "deadlocks": (0, None),
              "lock_p95_upper_ms": (100, None), "v3_backlog": (100, None),
              "drain_seconds": (60, None), "wal_mib_per_120s": (None, 32),
              "database_mib_per_120s": (None, 64)}
    baseline, shadow = phases
    metrics = []
    for phase in phases:
        metrics.append({
            **phase["resources"], "collector_p95_ms": phase["collector"]["p95_ms"],
            "query_p95_ms": phase["query"]["p95_ms"], "live_lag_seconds": phase["live_lag_seconds"],
            "drain_seconds": phase["drain_seconds"],
            "v3_backlog": max(phase["v3_combined_backlog_at_enqueue"], phase["resources"]["v3_worker_backlog"])
            if phase["resources"]["v3_worker_backlog"] is not None else None,
        })
    qualified = {
        "connections": all(row.get("connection_count_scope") == CONNECTION_COUNT_SCOPE for row in metrics),
        "lock_p95_upper_ms": all(type(row.get("lock_waiters_at_end")) is int and row["lock_waiters_at_end"] == 0
                                 for row in metrics),
    }
    gates = {}
    for key, (absolute, delta) in limits.items():
        left, right = [row.get(key) for row in metrics]
        if not qualified.get(key, True) or any(
            type(value) not in {int, float} or not math.isfinite(value) for value in (left, right)
        ):
            status = "UNRUN"
        elif (absolute is not None and max(left, right) > absolute) or (delta is not None and right - left > delta):
            status = "FAIL"
        else:
            status = "PASS"
        gates[key] = {"v2_only": left, "shadow": right, "limit": absolute, "delta_limit": delta, "status": status}
    equality = baseline["report_oracle"] == shadow["report_oracle"]
    gates["v2_report_stable"] = {"status": "PASS" if equality else "FAIL"}
    same_resources = all(baseline["resources"][key] == shadow["resources"][key] for key in
                         ("pg_revision", "core_revision", "postgres", "versions", "pg_target_sha256", "analytics_pool_by_role"))
    durations_match = abs(baseline["measurement_seconds_observed"] - shadow["measurement_seconds_observed"]) <= 5
    gates["comparable_runtime_and_interval"] = {"status": "PASS" if same_resources and durations_match else "UNRUN"}
    complete_updates = all(phase["resources"]["window_updates"] is not None for phase in phases)
    gates["observed_updates"] = {"status": "PASS" if complete_updates else "UNRUN"}
    complete_indexes = all(phase["resources"].get("index_footprint") for phase in phases)
    gates["observed_index_footprint"] = {"status": "PASS" if complete_indexes else "UNRUN"}
    states = {row["status"] for row in gates.values()}
    return ("FAIL" if "FAIL" in states else "UNRUN" if "UNRUN" in states else "PASS"), gates


def setup_plan(args, courses):
    profile = base.profile_load(args.profile)
    return Plan(profile, args.stage, args.pair, args.anchor, args.mode, courses)


def plan_evidence(plan, minutes, openings, mode):
    driver_files = (Path(__file__), Path(__file__).with_name("benchmark_windows_target.py"))
    driver_hash = hashlib.sha256(b"".join(path.read_bytes() for path in driver_files)).hexdigest()
    return dict(status="UNRUN", pair=str(plan.run_id), seed=plan.seed, profile="analytics-synthetic-profile-v1",
                driver_sha256=driver_hash,
                mode=mode, stage=plan.stage, anchor=plan.anchor.isoformat(), warmup_seconds=plan.warmup,
                measurement_seconds=plan.duration, active=plan.active, overlap=plan.overlap, cadence_seconds=60,
                v2_input_sha256=work_hash([*openings, *(item for minute in minutes for item in minute)]),
                planned_v2_measurement=(plan.active + plan.overlap) * (plan.duration // 60),
                planned_query_measurement=3 * (plan.duration // 30) + plan.active * (plan.duration // 60),
                planned_v3_measurement=plan.active * (plan.duration // 60),
                expected_window_keys=len(plan.expected_windows()), phases=[])


async def run(args, manifest, result):
    plan = setup_plan(args, [Course(**entry) for entry in manifest["courses"]])
    minutes = [tuple(plan.v2(i)) for i in range(plan.minutes)]
    openings = tuple(plan.v2(-1))
    snapshots = defaultdict(list)
    for minute, subject, snapshot in plan.snapshots(minutes):
        snapshots[minute].append((subject, snapshot))
    expected = plan.expected_windows()
    result.update(plan_evidence(plan, minutes, openings, args.mode))
    facts = [AcceptedEvent.model_validate_json(json.dumps(
        dict(producer_namespace="browser:" + str(subject), subject_ref=str(subject), source="browser",
             event=event.model_dump(mode="json"), received_at=event.ended_at.isoformat())))
        for subject, event in [*openings, *(item for minute in minutes for item in minute)]]
    modes = (args.phase,) if args.phase else ("v2_only", "shadow")
    instances = [manifest["targets"][mode]["instance"] for mode in ("v2_only", "shadow")]
    if len(set(instances)) != 2:
        raise base.StopRun("fresh separate baseline and shadow databases required")
    targets = [Target.from_record(manifest, mode, plan) for mode in modes]
    if len(targets) == 2 and set(targets[0].ports.values()) & set(targets[1].ports.values()):
        raise base.StopRun("monolithic pair requires separately identified runtime listeners")
    if not timedelta(minutes=24) <= datetime.now(UTC) - plan.anchor <= timedelta(hours=4):
        raise base.StopRun("anchor must place all synthetic intervals before both live phases")
    for target in targets:
        with phase_observer(target) as observer:
            preflight = {"mode": "shadow" if target.shadow else "v2_only", "status": "UNRUN",
                         "analytics_pool_by_role": target.analytics_pool_by_role}
            result.setdefault("preflight", []).append(preflight)
            try:
                observer.preflight()
                preflight["status"] = "PASS"
            finally:
                preflight["resources"] = observer.monitor.result()
            details = {"mode": "shadow" if target.shadow else "v2_only", "status": "RUNNING",
                       "database_sha256": hashlib.sha256(target.database.encode("ascii")).hexdigest(),
                       "core_database_sha256": hashlib.sha256(target.core_database.encode("ascii")).hexdigest()}
            result["phases"].append(details)
            phase = Phase(target, plan, details)
            try:
                report = await phase.workload(minutes, snapshots, openings, observer)
                with guarded_pg(target) as connection:
                    actual = observer.windows(connection)
                phase.finish(report, facts, expected, actual)
                details["resources"] = observer.result()
                hours = plan.stage * plan.duration / 3600
                details["updates_per_learner_hour"] = (
                    details["resources"]["window_updates"] / hours
                    if details["resources"]["window_updates"] is not None else None
                )
                details["committed_changes_per_learner_hour"] = phase.measured_existing_key_commits / hours
                details["unique_rows_added_per_learner_hour"] = details["resources"]["window_rows_added"] / hours
                details["status"] = "OBSERVED"
            finally:
                details.update(collector_partial=base.summary(phase.collector_ms), query_partial=base.summary(phase.query_ms),
                               v3_ack_partial=base.summary(phase.commit_ms), committed_v3_keys=len(phase.receipts))
                if details["status"] == "RUNNING":
                    details["status"] = "INTERRUPTED"
    if args.phase:
        result.update(status="OBSERVED", pairing="UNRUN")
    else:
        verdict, gates = assess(result["phases"])
        result.update(status=verdict, gates=gates, acceptance_607G="UNRUN", repeat_acceptance="UNRUN", cohort_expansion="UNRUN")


PAIR_FIELDS = ("pair", "seed", "profile", "driver_sha256", "mode", "stage", "anchor", "warmup_seconds", "measurement_seconds",
               "active", "overlap", "cadence_seconds", "v2_input_sha256", "planned_v2_measurement",
               "planned_query_measurement", "planned_v3_measurement", "expected_window_keys")


def combine(baseline, shadow):
    if any(baseline.get(key) is None or baseline.get(key) != shadow.get(key) for key in PAIR_FIELDS):
        raise ValueError("phase artifacts have different workload plans")
    phases = []
    for evidence, mode in zip((baseline, shadow), ("v2_only", "shadow"), strict=True):
        if evidence["status"] != "OBSERVED" or len(evidence["phases"]) != 1:
            raise ValueError("completed single-phase observations required")
        phase = evidence["phases"][0]
        pg = phase["pg_window_oracle"]
        report = phase["report_oracle"]
        expected_keys = evidence["expected_window_keys"]
        expected_active = evidence["active"] * evidence["measurement_seconds"] * 1000
        if (phase["mode"] != mode or phase["status"] != "OBSERVED" or pg["status"] != "PASS" or expected_keys <= 0
                or pg["denominator"] != expected_keys or pg["observed"] != (expected_keys if mode == "shadow" else 0)
                or report["status"] != "PASS" or expected_active <= 0 or report["totals"]["active_ms"] != expected_active
                or phase["collector"]["count"] != evidence["planned_v2_measurement"]
                or phase["query"]["count"] != evidence["planned_query_measurement"]
                or phase["v3_core_to_commit_ack"]["count"] != (evidence["planned_v3_measurement"] if mode == "shadow" else 0)
                or phase["resources"]["samples"] < 2):
            raise ValueError("phase kind, denominator or observations do not match")
        phases.append(phase)
    if any(phases[0][key] == phases[1][key] for key in ("database_sha256", "core_database_sha256")):
        raise ValueError("the phases shared the same writable database")
    verdict, gates = assess(phases)
    return dict(**{key: baseline[key] for key in PAIR_FIELDS}, phases=phases, status=verdict, gates=gates,
                acceptance_607G="UNRUN", repeat_acceptance="UNRUN", cohort_expansion="UNRUN")


def compare_main(argv):
    cli = argparse.ArgumentParser(description="Compare observations from separately staged CORP-607 phases.")
    cli.add_argument("baseline", type=Path)
    cli.add_argument("shadow", type=Path)
    cli.add_argument("--output", required=True, type=Path)
    args = cli.parse_args(argv)
    try:
        output = base.output_path(args.output)
        for path in (args.baseline, args.shadow):
            if path.is_symlink() or path.resolve().parent != output.parent or path.stat().st_size > 2_000_000:
                raise ValueError
        records = [json.loads(path.read_text(encoding="utf-8")) for path in (args.baseline, args.shadow)]
        result = combine(*records)
        base.write_result(output, result)
    except (OSError, KeyError, ValueError, TypeError):
        cli.error("matching, complete single-phase evidence under .local/benchmark-results required")
    print(json.dumps({"status": result["status"], "phases": 2}))
    return {"PASS": 0, "FAIL": 1, "UNRUN": 2}[result["status"]]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "compare":
        return compare_main(argv[1:])
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--profile", type=Path, default=base.ROOT / "bench/profile-v1.json")
    cli.add_argument("--stage", type=int, required=True, choices=base.STAGES)
    cli.add_argument("--mode", choices=MODES, required=True)
    cli.add_argument("--pair", type=UUID, required=True)
    cli.add_argument("--anchor", type=datetime.fromisoformat, required=True)
    cli.add_argument("--phase", choices=("v2_only", "shadow"))
    cli.add_argument("--manifest", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--run", action="store_true")
    args = cli.parse_args(argv)
    result = {}
    if not args.run:
        if args.manifest or args.output or args.phase:
            cli.error("dry run does not read private manifests or write files")
        courses = [Course("synthetic-a", "dry-v1", "lesson-a"), Course("synthetic-b", "dry-v1", "lesson-b")]
        plan = setup_plan(args, courses)
        result = plan_evidence(plan, [tuple(plan.v2(i)) for i in range(plan.minutes)], tuple(plan.v2(-1)), args.mode)
        result["synthetic_learners_for_owner"] = [str(user) for user in plan.users]
        print(json.dumps(result, indent=2))
        return 0
    if not args.manifest or not args.output or os.environ.get("ANALYTICS_BENCHMARK_GO") != "1":
        cli.error("live run requires private --manifest, new --output and explicit ANALYTICS_BENCHMARK_GO=1")
    try:
        args.output = base.output_path(args.output)
        manifest = private_manifest(args.manifest, args.pair)
    except Exception:
        cli.error("invalid output or private owner manifest")
    exit_code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as artifact:
        try:
            asyncio.run(run(args, manifest, result))
            exit_code = {"PASS": 0, "FAIL": 1, "UNRUN": 2, "OBSERVED": 0}[result["status"]]
        except base.ObservedViolation as error:
            result.update(status="FAIL", stop_reason=str(error))
            exit_code = 1
        except base.StopRun as error:
            result.update(status="UNRUN", stop_reason=str(error))
        except BaseException as error:
            result.update(status="UNRUN", stop_kind=type(error).__name__)
        finally:
            json.dump(result, artifact, indent=2, allow_nan=False)
            artifact.write("\n")
    print(json.dumps({"status": result["status"], "phases": len(result.get("phases", ()))}, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

