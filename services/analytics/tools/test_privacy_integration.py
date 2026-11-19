"""Run the signed HTTP/PG synthetic deletion scenario on fresh guarded DBs."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    temporary = ROOT / ".local" / ("pytest-privacy-integration-" + uuid4().hex)
    temporary.parent.mkdir(exist_ok=True)
    env = dict(os.environ, ANALYTICS_RUN_SYNTHETIC_PRIVACY_INTEGRATION="1", PYTHONDONTWRITEBYTECODE="1")
    raise SystemExit(subprocess.call([sys.executable, "-B", "-m", "pytest", "tests/privacy_integration",
                                     "-q", "--tb=short", "-p", "no:cacheprovider", "--basetemp", str(temporary)], cwd=ROOT, env=env))
