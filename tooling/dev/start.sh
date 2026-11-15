#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$repo_root/services/core-api/.venv/bin/python" "$repo_root/tooling/dev/run.py" "$@"
