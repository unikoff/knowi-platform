"""Run replay tests against new guarded synthetic PostgreSQL DBs only."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    env = os.environ.copy()
    env["ANALYTICS_RUN_SYNTHETIC_REPLAY"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(subprocess.call([sys.executable, "-B", "-m", "pytest", "tests/replay_postgres",
                                     "-q", "--tb=line", "-p", "no:cacheprovider"], cwd=ROOT, env=env))
