"""Code, course resources and writable state locations; no database or dotenv I/O."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SERVICE_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ROOT = SERVICE_ROOT
BACKEND_ROOT = LEGACY_ROOT
DIST_DIR = PROJECT_ROOT / "apps" / "web" / "dist"
MATERIALS_ROOT = PROJECT_ROOT / "courses" / "backend" / "backend" / "plans"
COURSES_ROOT = Path(os.environ.get("PYMENTOR_COURSES_ROOT", str(PROJECT_ROOT / "courses")))
if not COURSES_ROOT.is_absolute():
    raise ValueError("PYMENTOR_COURSES_ROOT must be absolute")


def state_directory():
    configured = os.environ.get("PYMENTOR_STATE_DIR", "").strip()
    path = Path(configured) if configured else SERVICE_ROOT / ".local" / "state"
    if not path.is_absolute():
        raise ValueError("PYMENTOR_STATE_DIR must be absolute")
    return path
