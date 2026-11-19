"""Run guarded integration tests against fresh synthetic databases only."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    env = os.environ.copy()
    env["ANALYTICS_RUN_SYNTHETIC_BACKPRESSURE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(subprocess.call([sys.executable, "-B", "-m", "pytest", "tests/backpressure_postgres",
                                     "-q", "--tb=line", "-p", "no:cacheprovider"], cwd=ROOT, env=env))
