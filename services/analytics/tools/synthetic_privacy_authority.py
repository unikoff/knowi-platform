"""Thin local wrapper; implementation is included in the Analytics image."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymentor_analytics.adapters.privacy_authority.server import main

if __name__ == "__main__":
    main()
