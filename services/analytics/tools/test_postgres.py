"""Run live tests only against the designated locally provisioned synthetic cluster."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    env = os.environ.copy()
    env["ANALYTICS_RUN_SYNTHETIC_POSTGRES"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    temporary = (ROOT / ".local" / "pytest" / uuid4().hex).resolve()
    if not temporary.is_relative_to((ROOT / ".local").resolve()) or temporary.exists():
        raise SystemExit("Unsafe temporary test target")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(subprocess.call([sys.executable, "-B", "-m", "pytest", "tests/postgres", "-q", "-p", "no:cacheprovider", "--basetemp", str(temporary)], cwd=ROOT, env=env))
