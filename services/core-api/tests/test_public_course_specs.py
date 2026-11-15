from copy import deepcopy
import json
from pathlib import Path

import pytest

from pymentor_core.modules.practice.public_specs import public_submit_result, public_task, PUBLIC_TASK_FIELDS
from pymentor_core.adapters.course_local.runtime import default_runtime


SECRET = "MC205_PRIVATE_JUDGE_SENTINEL_8a51d96e"


def test_canaries_in_unknown_top_level_and_nested_private_fields_never_export():
    task = {"id": "task", "title": "Visible", "prompt": "Public instruction", "revision": "a" * 64, "tests": [{"expected": SECRET}], "reference_code": SECRET, "future_private_field": SECRET, "requirements": {"nodes": ["If"], "secret": SECRET}, "contract": {"given": "Public example", "tests": SECRET}, "hints": ["Visible hint"]}
    projected = public_task(task)
    assert SECRET not in json.dumps(projected)
    assert projected["requirements"] == {"nodes": ["If"]}
    assert projected["contract"] == {"given": "Public example"}
    assert set(projected) <= PUBLIC_TASK_FIELDS
    assert task["tests"][0]["expected"] == SECRET


@pytest.mark.parametrize("extra", [{"hints": [{"secret": SECRET}]}, {"requirements": {"nodes": [{"secret": SECRET}]}}, {"contract": {"given": {"secret": SECRET}}}, {"prompt": {"secret": SECRET}}])
def test_malformed_public_structures_fail_closed(extra):
    with pytest.raises(ValueError):
        public_task({"id": "task", **extra})


def test_existing_full_specs_keep_exact_public_contract_in_both_products():
    for course in ("backend", "backend_copy"):
        snapshot = default_runtime().snapshot(course)
        count = 0
        for lesson in snapshot.catalog.LESSONS:
            for task in lesson["tasks"]:
                legacy_public = {key: deepcopy(value) for key, value in task.items() if key not in {"tests", "legacy_ids"}}
                assert public_task(task) == legacy_public
                returned = public_task(task)
                returned.get("hints", []).append("mutation")
                assert task.get("hints", []) == legacy_public.get("hints", [])
                count += 1
        assert count == 204


@pytest.mark.parametrize("result", [
    {"ok": False, "score": 10, "stdout": SECRET, "stderr": SECRET, "traceback": SECRET, "error": SECRET, "tests": [{"name": SECRET, "passed": False, "expected": SECRET, "actual": SECRET, "error": SECRET}]},
    {"ok": True, "score": 100, "stdout": SECRET, "tests": [{"name": SECRET, "passed": True, "expected": SECRET, "actual": SECRET}]},
    {"ok": False, "error_kind": "requirements", "error": SECRET},
])
def test_judge_values_and_text_never_escape_success_or_error_results(result):
    public = public_submit_result(result)
    assert SECRET not in json.dumps(public)
    assert public["ok"] is result["ok"]
    assert public["stdout"] == ""
    assert public["traceback"] is None and public["stderr"] is None
    for test in public["tests"]:
        assert not {"expected", "actual"}.intersection(test)


def test_frontend_discovery_cannot_import_server_course_paths():
    root = Path(__file__).resolve().parents[3]
    # Current and later relocated frontend source must not reference server files.
    frontend_roots = [root / "apps/web/src", *(root / "courses").glob("*/frontend")]
    forbidden = ("personal.settings.yaml", "/backend/practice/", "/backend/plans/", "PYMENTOR_LANDING_BOT_CREDENTIALS")
    for source_root in frontend_roots:
        for path in source_root.rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".mjs"}:
                content = path.read_text(encoding="utf-8")
                assert not any(marker in content for marker in forbidden), path.relative_to(root)
