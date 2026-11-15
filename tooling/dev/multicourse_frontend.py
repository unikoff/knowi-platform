"""Start only this isolated workspace's Vite frontend with its safe local API proxy."""

from __future__ import annotations

import os
import argparse
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "apps" / "web"
SAFE_API_TARGET = "http://127.0.0.1:18001"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18517)
    parser.add_argument("--base", choices=("/", "/platform/"), default="/")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port in {8001, 18001}:
        parser.error("choose a separate frontend port; protected API ports are forbidden")
    env = os.environ.copy()
    env["PYMENTOR_DEV_API_TARGET"] = SAFE_API_TARGET
    env["VITE_BASE"] = args.base
    env["VITE_CACHE_DIR"] = str(FRONTEND_ROOT / ".cache" / ("multicourse-vite-platform" if args.base == "/platform/" else "multicourse-vite-root"))
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return subprocess.run([npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(args.port)], cwd=FRONTEND_ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
