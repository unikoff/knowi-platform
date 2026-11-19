"""Run local structural checks; no Docker daemon or cluster mutation."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
result = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "services/analytics/tests/deployment", "-v"],
    cwd=root,
    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
)
raise SystemExit(result.returncode)
