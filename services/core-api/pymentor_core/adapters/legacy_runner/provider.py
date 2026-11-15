from pymentor_core.adapters.legacy_runner import python as legacy_python
from pymentor_core.adapters.legacy_runner.python import run_python_code
from pymentor_core.adapters.legacy_runner.python import run_python_preview
from pymentor_core.adapters.legacy_runner.python import run_python_task
from pymentor_core.adapters.legacy_runner.executor import get_runner_executor


class LegacyRunnerPolicy:
    max_code_length = legacy_python.MAX_CODE_LENGTH
    blocked_reason = staticmethod(legacy_python._blocked_reason)
    syntax_error_reason = staticmethod(legacy_python._syntax_error_reason)
    requirements_error = staticmethod(legacy_python._requirements_error)
    jsonable = staticmethod(legacy_python._jsonable)


class LegacyRunner:
    policy = LegacyRunnerPolicy()
    run_python_code = staticmethod(run_python_code)
    run_python_preview = staticmethod(run_python_preview)
    run_python_task = staticmethod(run_python_task)
    get_runner_executor = staticmethod(get_runner_executor)
