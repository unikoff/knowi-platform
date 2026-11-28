"""Repository entry point for the packaged historical transfer command."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymentor_analytics.app.backfill_windows_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
