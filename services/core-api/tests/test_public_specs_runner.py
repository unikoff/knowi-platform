"""Real local runner proof for the public demo and hidden judge boundary."""

import json

from pymentor_core.adapters.legacy_runner.python import run_python_preview, run_python_task
from pymentor_core.modules.practice.public_specs import public_submit_result


SECRET = "MC205_HIDDEN_CASE_102f24f1"
TASK = {"mode": "solve", "tests": [
    {"name": "Public demonstration", "args": [1], "expected": 1},
    {"name": SECRET, "args": [SECRET], "expected": SECRET},
]}


def test_preview_executes_only_the_existing_public_demonstration_case():
    result = run_python_preview("def solve(value):\n    return value", TASK)
    assert result["ok"] is True
    assert result["stdout"] == "1"
    assert SECRET not in json.dumps(result)


def test_real_failed_judge_error_values_do_not_escape_public_submit_result(caplog):
    raw = run_python_task("def solve(value):\n    raise ValueError(str(value))", TASK)
    assert raw["ok"] is False
    assert SECRET in json.dumps(raw)
    safe = public_submit_result(raw)
    assert safe["ok"] is False
    assert SECRET not in json.dumps(safe)
    assert len(safe["tests"]) == 2
    assert SECRET not in caplog.text
