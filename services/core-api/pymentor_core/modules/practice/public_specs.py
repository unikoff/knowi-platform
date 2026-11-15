"""Explicit public practice projections; judge inputs never become response data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, TypedDict


class PublicTaskDTO(TypedDict, total=False):
    id: str
    revision: str
    title: str
    level: str
    mode: str
    prompt: str
    contract: dict[str, Any] | None
    requirements: dict[str, list[str]]
    hints: list[str]
    starter_code: str


class InternalJudgeSpec(PublicTaskDTO, total=False):
    tests: list[dict[str, Any]]
    legacy_ids: list[str]
    reference_code: str


PUBLIC_TASK_FIELDS = frozenset(PublicTaskDTO.__annotations__)
_REQUIREMENT_FIELDS = frozenset({"attributes", "calls", "items", "names", "nodes"})
_CONTRACT_FIELDS = frozenset({"check", "check_items", "given", "given_items", "todo", "todo_items"})


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"public task {field} must be an array of strings")
    return value.copy()


def public_task(task: InternalJudgeSpec) -> PublicTaskDTO:
    """Allowlist at each structured boundary, preserving existing optional fields."""
    result = {key: deepcopy(task[key]) for key in PUBLIC_TASK_FIELDS if key in task}
    if "requirements" in result:
        rules = result["requirements"]
        if not isinstance(rules, dict):
            raise ValueError("public task requirements must be an object")
        result["requirements"] = {key: _string_list(value, f"requirements.{key}") for key, value in rules.items() if key in _REQUIREMENT_FIELDS}
    if "contract" in result and result["contract"] is not None:
        contract = result["contract"]
        if not isinstance(contract, dict):
            raise ValueError("public task contract must be an object")
        filtered = {}
        for key, value in contract.items():
            if key not in _CONTRACT_FIELDS:
                continue
            if key.endswith("_items"):
                filtered[key] = _string_list(value, f"contract.{key}")
            elif isinstance(value, str):
                filtered[key] = value
            else:
                raise ValueError(f"public task contract.{key} must be text")
        result["contract"] = filtered
    if "hints" in result:
        result["hints"] = _string_list(result["hints"], "hints")
    for field in PUBLIC_TASK_FIELDS - {"requirements", "contract", "hints"}:
        if field in result and not isinstance(result[field], str):
            raise ValueError(f"public task {field} must be text")
    return result


_CHECK_ERROR = "Не удалось проверить решение. Проверьте код и попробуйте ещё раз."
_REQUIREMENTS_ERROR = "Решение не соответствует требованиям кода. Сверьте конструкции с условием задачи."
_FAILED_TEST = "Одна из проверок не пройдена. Сверьте решение с условием задачи."


def public_submit_result(result: Any) -> dict[str, Any]:
    """Hide judge-controlled text/values for every course/chapter, including errors."""
    result = result if isinstance(result, dict) else {}
    ok = result.get("ok") is True
    raw_score = result.get("score")
    score = raw_score if type(raw_score) is int and 0 <= raw_score <= 100 else 0
    tests = []
    raw_tests = result.get("tests")
    if isinstance(raw_tests, list):
        for index, test in enumerate(raw_tests, start=1):
            passed = isinstance(test, dict) and test.get("passed") is True
            item = {"name": f"Проверка {index}", "passed": passed}
            if not passed:
                item["error"] = _FAILED_TEST
            tests.append(item)
    failed = any(not test["passed"] for test in tests)
    show_error = bool(result.get("error")) or (not ok and (not tests or not failed))
    error = _CHECK_ERROR if show_error else None
    if not ok and result.get("error_kind") == "requirements":
        error = _REQUIREMENTS_ERROR
    return {"ok": ok, "score": score, "tests": tests, "stdout": "", "stderr": None, "error": error, "traceback": None}
