import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_profile", ROOT / "tools" / "check_profile.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def profile():
    return json.loads((ROOT / "bench" / "profile-v1.json").read_text(encoding="utf-8"))


def test_fixed_budget_arithmetic(profile):
    result = module.calculate(profile)
    assert result["connections"] == {"1/1/1": 12, "2/1/1": 14, "1/2/1": 14, "1/1/2": 14, "2/2/2": 18}
    assert (result["peak_streams"], result["events_per_second"], result["outage_events"]) == (36, 2.4, 207360)
    assert result["outage_bytes"] == 1274019840
    assert result["drain_seconds"] == 57600
    assert result["segment_events_per_measurement"] == 2160


def test_rejects_connection_overcommit(profile):
    profile["pg_max_connections"] = 17
    with pytest.raises(ValueError, match="connection budget"):
        module.calculate(profile)


def test_rejects_drain_no_faster_than_arrivals(profile):
    profile["assumed_drain_events_per_second"] = 2.4
    with pytest.raises(ValueError, match="drain"):
        module.calculate(profile)


def test_rejects_inadequate_storage_and_unknown_version(profile):
    profile["outbox_capacity_bytes"] = 1000
    with pytest.raises(ValueError, match="outage budget"):
        module.calculate(profile)
    profile["version"] = "unknown"
    with pytest.raises(ValueError, match="version"):
        module.calculate(profile)
