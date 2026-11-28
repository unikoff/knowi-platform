"""Summarize independent read-only 607 reports without conflating their runs."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re


def _bound_builds(proof, reports):
    if proof["schema_version"] != 1:
        raise ValueError("unsupported component proof")
    required = set(proof["required_shared"])
    if not required:
        raise ValueError("component proof must declare common required roles")
    by_run = {}
    for report, digest in reports:
        item = proof["runs"][report["run_id"]]
        if item["report_sha256"] != digest or not required.issubset(item["applicable"]):
            raise ValueError("component proof does not bind the actual report and roles")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in item["applicable"].values()):
            raise ValueError("component proof contains an invalid digest")
        by_run[report["run_id"]] = item["applicable"]
    return by_run


def combine(reports, *, applicable_builds=None):
    windows, sources, reasons = {}, [], Counter()
    grouped = {name: Counter() for name in ("normal", "fault", "unplanned")}
    for report in reports:
        if report["schema_version"] != 1 or report["mode"] != "read_only":
            raise ValueError("expected a read-only 607 report")
        if report["counts"]["denominator"] != len(report["windows"]):
            raise ValueError("source denominator differs from its key list")
        run = report["run_id"]
        sources.append({
            "run_id": run, "target": report["target"], "versions_sha256": report["versions_sha256"],
            "truncated": report["truncated"], "verdict": report["verdict"], "denominator": len(report["windows"]),
            "counts": report["counts"], "scope_data_verdict": report.get("scope_data_verdict", report["verdict"]),
        })
        for row in report["windows"]:
            key = row["key"]
            identity = (run, key["student_id"], key["activity_date"], key["slot_no"])
            if identity in windows:
                if windows[identity] != row:
                    raise ValueError("two observations of one run key disagree")
                continue
            windows[identity] = row
            totals = grouped[row["group"]]
            observed = row["observed_outcome"]
            totals["denominator"] += 1
            totals["verified"] += int(row["verified"])
            totals["old_only"] += int(observed == "old_only")
            totals["unknown"] += int(observed in {"unobserved", "both_lost", "unknown"})
            totals["v3_only"] += int("v3_only" in row["reasons"])
            totals["both_lost"] += int(observed == "both_lost")
            totals["conflict"] += int(bool({"writer_conflict", "catalog_conflict"}.intersection(row["reasons"])))
            totals["mismatch_before"] += int(row["mismatch_before"] is True)
            totals["unknown_before"] += int(row["mismatch_before"] is None)
            totals["mismatch_after"] += int(row["mismatch_after"] is True)
            totals["unknown_after"] += int(row["mismatch_after"] is None)
            totals["open"] += int(not row["accepted"])
            reasons.update(row["reasons"])

    fields = ("denominator", "verified", "old_only", "unknown", "v3_only", "both_lost", "conflict",
              "mismatch_before", "unknown_before", "mismatch_after", "unknown_after", "open")
    zero = dict.fromkeys(fields, 0)
    summed = Counter(zero)
    for totals in grouped.values():
        summed.update(totals)
    builds = {source["versions_sha256"] for source in sources}
    comparable = len(builds) == 1 and None not in builds
    differing = {}
    if applicable_builds is not None:
        names = {name for components in applicable_builds.values() for name in components}
        for name in names:
            values = {run: components[name] for run, components in applicable_builds.items() if name in components}
            if len(set(values.values())) > 1:
                differing[name] = values
        comparable = bool(applicable_builds) and not differing
    data_passed = bool(windows) and all(source["scope_data_verdict"] == "pass" for source in sources)
    all_passed = data_passed and comparable and all(source["verdict"] == "pass" for source in sources)
    ordered_windows = [{"run_id": key[0], **row} for key, row in sorted(windows.items())]
    return {
        "schema_version": 1, "verdict": "pass" if all_passed else "open",
        "scope_data_verdict": "pass" if data_passed else "open",
        "one_version": comparable, "applicable_builds": applicable_builds, "version_conflicts": differing,
        "counts": dict(summed),
        "groups": {group: {**zero, **totals} for group, totals in grouped.items()},
        "reason_counts": dict(reasons), "sources": sources, "windows": ordered_windows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--component-proof", type=Path, help="run-specific hashes of actually applicable components")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payloads = [path.read_bytes() for path in args.report]
        reports = [(json.loads(data.decode("utf-8-sig")), sha256(data).hexdigest()) for data in payloads]
        proof = args.component_proof.read_bytes() if args.component_proof else None
        builds = _bound_builds(json.loads(proof.decode("utf-8-sig")), reports) if proof is not None else None
        merged = combine((report for report, _ in reports), applicable_builds=builds)
        merged["input_sha256"] = [{"file": path.name, "sha256": digest}
                                  for path, (_, digest) in zip(args.report, reports, strict=True)]
        merged["component_proof_sha256"] = sha256(proof).hexdigest() if proof is not None else None
        body = json.dumps(merged, indent=2, sort_keys=True) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(body)
        else:
            print(body, end="")
    except (KeyError, OSError, TypeError, ValueError):
        print(json.dumps({"verdict": "unavailable", "error": "incompatible run reports"}))
        return 2
    return 0 if merged["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
