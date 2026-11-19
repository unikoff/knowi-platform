"""Run only disposable, guarded integration databases on the synthetic cluster."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    env = os.environ.copy()
    env["ANALYTICS_RUN_SYNTHETIC_INTAKE"] = "1"
    raise SystemExit(subprocess.call([sys.executable, "-B", "-m", "pytest", "tests/intake_postgres",
                                     "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=env))
