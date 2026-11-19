"""Validate the frozen synthetic profile and its capacity arithmetic; no I/O to services."""
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[1] / "bench" / "profile-v1.json"


def calculate(p, *, core_transfer=False):
    if p["version"] != "analytics-synthetic-profile-v1":
        raise ValueError("unsupported profile version")
    if p["users"] != [10, 15, 25, 50] or p["results"] != "UNRUN":
        raise ValueError("unexpected stages or result state")
    if not 0 < p["active_ratio"] <= 1 or not 0 <= p["overlap_ratio"] <= 1:
        raise ValueError("invalid ratios")
    if not 0 < p["warning_fraction"] < p["critical_fraction"] < 1:
        raise ValueError("invalid thresholds")
    positive = ("heartbeat_seconds", "measurement_seconds", "pool_per_process", "pg_max_connections", "outbox_event_bytes_with_overhead", "outbox_index_storage_factor", "outage_hours", "outbox_capacity_bytes")
    if any(p[k] <= 0 for k in positive) or p["pool_overflow"] != 0:
        raise ValueError("invalid budget")
    reserve = sum(p[k] for k in ("privacy_reserve", "migration_reserve", "admin_reserve", "backup_reserve"))
    per = p["pool_per_process"] + p["pool_overflow"]
    if not isinstance(core_transfer, bool):
        raise ValueError("invalid transfer flag")
    status = 1 if core_transfer else 0  # One separately bounded status pool per Collector.
    matrix = {"1/1/1": 3 * per + status + reserve,
              "2/1/1": 4 * per + 2 * status + reserve,
              "1/2/1": 4 * per + status + reserve,
              "1/1/2": 4 * per + status + reserve,
              "2/2/2": 6 * per + 2 * status + reserve}
    if max(matrix.values()) > p["pg_max_connections"]:
        raise ValueError("connection budget exceeds max_connections")
    # Whole active subjects and overlapping devices; rounding up is conservative.
    active = math.ceil(max(p["users"]) * p["active_ratio"])
    streams = active + math.ceil(active * p["overlap_ratio"])
    rate = Fraction(streams, p["heartbeat_seconds"])
    events = math.ceil(rate * p["outage_hours"] * 3600)
    storage = events * p["outbox_event_bytes_with_overhead"] * p["outbox_index_storage_factor"]
    net_drain = Fraction(str(p["assumed_drain_events_per_second"])) - rate
    if net_drain <= 0 or storage > p["outbox_capacity_bytes"] * p["critical_fraction"]:
        raise ValueError("outage budget or drain assumption is infeasible")
    return {"connections": matrix, "peak_active": active, "peak_streams": streams, "events_per_second": float(rate), "outage_events": events, "outage_bytes": storage, "drain_seconds": float(events / net_drain), "segment_events_per_measurement": math.ceil(rate * p["measurement_seconds"])}


def main():
    try:
        result = calculate(json.loads(PROFILE.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
