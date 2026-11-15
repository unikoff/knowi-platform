"""Exercise the injected runner policy on local and remote paths."""
from unittest.mock import patch

from pymentor_core.adapters.legacy_runner.provider import LegacyRunner
from pymentor_core.modules.learning.api import service


def test_remote_script_uses_injected_policy_and_projects_results():
    task = {"mode": "script", "tests": [{"assert": "stdout", "expected": "hello\n"}]}
    runner = LegacyRunner()
    with patch.object(service, "get_runner_policy", return_value=runner.policy), \
         patch.object(service, "_remote_call", return_value={"ok": True, "stdout": "hello\n", "error": None}) as call:
        result = service._remote_task("http://runner", service.run_python_task, "print('hello')", task)
        assert result["ok"] and result["score"] == 100
        assert result["tests"][0]["actual"] == "hello\n"
        call.assert_called_once()
        result = service._remote_task("http://runner", service.run_python_task, "import os", task)
        assert not result["ok"] and result["error"]
        call.assert_called_once()


def test_remote_solve_serializes_and_projects_observation():
    task = {"mode": "solve", "tests": [{"args": [2], "expected": 4}]}
    response = {"ok": True, "error": None, "stdout": '{"setup":"","observations":[{"actual":["int",4],"args":["list",[["int",2]]],"kwargs":["dict",[]],"stdout":"","error":null}],"error":null}'}
    with patch.object(service, "get_runner_policy", return_value=LegacyRunner().policy), \
         patch.object(service, "_remote_call", return_value=response) as call:
        result = service._remote_task("http://runner", service.run_python_task, "def solve(x): return x * 2", task)
    assert result["ok"] and result["tests"][0]["actual"] == 4
    assert 'PAYLOAD' in call.call_args.args[1]


def test_local_runner_success_and_error():
    runner = LegacyRunner()
    assert runner.run_python_code("print(42)")["stdout"] == "42\n"
    failure = runner.run_python_code("raise ValueError('bad')")
    assert not failure["ok"] and "ValueError" in failure["error"]


def test_execution_routes_local_and_remote():
    import asyncio

    class Executor:
        async def run(self, function, **kwargs):
            return {"route": "local", "function": function, **kwargs}

    task = {"mode": "script"}
    with patch.object(service, "_remote_url", return_value=""), \
         patch.object(service, "_require_runner_enabled"), \
         patch.object(service, "get_runner_executor", return_value=Executor()):
        local = asyncio.run(service._execute_task(service.run_python_task, "pass", task))
    assert local == {"route": "local", "function": service.run_python_task, "code": "pass", "task": task}
    with patch.object(service, "_remote_url", return_value="http://runner"), \
         patch.object(service, "_remote_task", return_value={"ok": False, "error": "failed"}) as remote:
        result = asyncio.run(service._execute_task(service.run_python_task, "pass", task))
    assert result == {"ok": False, "error": "failed"}
    remote.assert_called_once_with("http://runner", service.run_python_task, "pass", task)
