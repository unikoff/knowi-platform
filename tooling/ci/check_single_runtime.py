"""Fail the release gate if SQLite re-enters serving sources, config or image."""
import argparse
import ast
import json
import os
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RETIRED_PATHS = (
    "backend",
    "passenger_wsgi.py",
    "backend/main.py", "backend/_core_entrypoint.py", "backend/sitecustomize.py",
    "backend/notifications/worker.py", "backend/services/notification_worker.py", "backend/Dockerfile",
    "services/core-api/pymentor_core/app/passenger.py", "infra/helm/pymentor/templates/pvc.yaml",
)
CONFIGS = (
    ".github/workflows/release.yml", "services/core-api/Dockerfile",
    "tooling/ci/preview_checks.py", "infra/local-k3s/build-images.sh", "infra/local-k3s/cluster.sh",
    "infra/helm/pymentor/templates/deployment-core.yaml", "infra/local-postgres-redis/compose.yaml",
)

RETIRED_SOURCE_PATHS = (
    "services/core-api/tools/migration",
    "services/core-api/tools/lab/storage",
    "services/core-api/tools/lab/session_models",
    "services/core-api/pymentor_core/adapters/postgres/legacy_sessions.py",
    "services/core-api/tools/maintenance/fill_booking_slots.py",
)
RETIRED_MODULES = (
    "tools.migration", "tools.lab.storage", "tools.lab.session_models",
    "tools.lab.c232", "tools.lab.inventory_sqlite_snapshot",
    "pymentor_core.adapters.postgres.legacy_sessions",
)

# Independent Analytics privacy journal and its dedicated test/tool consumers.
# This exception applies only to sqlite3 and SQLite engine literals in these
# exact files; retired Core imports and paths are still checked everywhere.
ANALYTICS_SQLITE_FILES = frozenset({
    "services/analytics/pymentor_analytics/adapters/privacy_authority/journal.py",
    "services/analytics/tests/activity_projection/test_projection_report_rollups_offline.py",
    "services/analytics/tests/core_local_privacy/test_local_client.py",
    "services/analytics/tests/cross_service/chain_checks.py",
    "services/analytics/tests/cross_service/cleanup_failed_run.py",
    "services/analytics/tests/cross_service/test_chain_offline.py",
    "services/analytics/tests/cross_service/test_live_http.py",
    "services/analytics/tests/privacy_authority/test_authority.py",
    "services/analytics/tools/test_cross_service.py",
    "services/analytics/tools/legacy_preservation/runtime.py",
})


def scan_retired_sources(root):
    """Check tools and tests as well as runtime; content is an independent course."""
    root = Path(root)
    violations = [f"{name}:retired-sqlite-source-present"
                  for name in RETIRED_SOURCE_PATHS if (root / name).exists()]
    for directory in ("services", "tooling"):
        paths = []
        for current, directories, files in os.walk(root / directory, followlinks=False):
            directories[:] = [name for name in directories
                              if name not in {"__pycache__", "node_modules", ".venv", ".local", ".cache", "dist", "output"}
                              and not name.startswith(".pytest")]
            paths.extend(Path(current) / name for name in files if name.endswith(".py"))
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if relative.startswith("services/core-api/content/") or any(
                part in {"__pycache__", "node_modules", ".venv", "output"}
                or part.startswith(".pytest") for part in path.relative_to(root).parts
            ):
                continue
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                    names += [(node.module or "") + "." + alias.name for alias in node.names]
                if any((name == "sqlite3" and relative not in ANALYTICS_SQLITE_FILES) or name.startswith(RETIRED_MODULES) for name in names):
                    violations.append(f"{relative}:{node.lineno}:retired-source-import")
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
                if name == "import_module" and node.args and isinstance(node.args[0], ast.Constant):
                    module = node.args[0].value
                    if isinstance(module, str) and ((module == "sqlite3" and relative not in ANALYTICS_SQLITE_FILES) or module.startswith(RETIRED_MODULES)):
                        violations.append(f"{relative}:{node.lineno}:dynamic-retired-import")
                if relative not in ANALYTICS_SQLITE_FILES and name in {"create_engine", "create_async_engine"} and node.args:
                    dsn = ast.get_source_segment(source, node.args[0]) or ""
                    if re.search(r"sqlite(?:\+[a-z0-9_]+)?://", dsn.lower()):
                        violations.append(f"{relative}:{node.lineno}:executable-sqlite-fixture")
    return violations


def scan_package(package):
    package = Path(package)
    violations = []
    if (package / "adapters/legacy_sqlite").exists():
        violations.append("adapters/legacy_sqlite:retired-runtime-adapter-present")
    files = list(package.rglob("*.py"))
    if not files:
        return ["missing-or-empty-runtime-package"]
    for path in files:
        relative = path.relative_to(package).as_posix()
        if path.is_symlink():
            violations.append(f"{relative}:symlinked-runtime-source")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeError):
            violations.append(f"{relative}:invalid-runtime-source")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import): names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom): names = [node.module or "", *[alias.name for alias in node.names]]
            if any("legacy_sqlite" in name or name in {"sqlite3", "SqlSessionRepository"} for name in names):
                violations.append(f"{relative}:{node.lineno}:sqlite-runtime-import")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search(r"sqlite(?:\+[a-z0-9_]+)?://", node.value.lower()):
                    violations.append(f"{relative}:{node.lineno}:sqlite-runtime-dsn")
                if node.value == "sqlite3" or "pymentor_core.adapters.legacy_sqlite" in node.value:
                    violations.append(f"{relative}:{node.lineno}:dynamic-sqlite-runtime-import")
            if isinstance(node, ast.ClassDef) and node.name == "SqlSessionRepository":
                violations.append(f"{relative}:{node.lineno}:sql-auth-runtime-repository")
    return violations


def scan_config(root):
    violations = [f"{name}:retired-runtime-source-present" for name in RETIRED_PATHS if (Path(root) / name).exists()]
    for name in CONFIGS:
        path = Path(root) / name
        if not path.is_file():
            violations.append(f"{name}:missing-active-config")
            continue
        source = path.read_text(encoding="utf-8").lower()
        for marker in ("sqlite://", "arg profile", "--build-arg profile", "backend/dockerfile", "continue-on-error: true"):
            if marker in source:
                violations.append(f"{name}:retired-or-optional-runtime-config")
    return violations


def check_image(image):
    code = """
import importlib.util, os, sys
from pathlib import Path
assert os.getuid() == 10001
assert not Path('/app/services/core-api/pymentor_core/adapters/legacy_sqlite').exists()
assert not Path('/app/services/core-api/pymentor_core/adapters/postgres/legacy_sessions.py').exists()
assert not Path('/app/services/core-api/tools').exists()
assert not Path('/app/services/core-api/pymentor_core/app/passenger.py').exists()
assert not Path('/app/backend/main.py').exists()
assert not list(Path('/app').rglob('*.sqlite*'))
assert importlib.util.find_spec('alembic') is None
from pymentor_core.app.factory import create_app
app = create_app()
assert app.state.startup_complete is False
assert not any('legacy_sqlite' in name for name in sys.modules)
print('PASS canonical image assembly without SQLite adapter or DDL tooling')
"""
    subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--env", "DATABASE_URL=postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/synthetic",
        "--env", "REDIS_URL=redis://127.0.0.1:9/0", "--env", "KNOWI_REDIS_PREFIX=ci-image:",
        "--env", "KNOWI_LOGIN_RECOVERY_KEY=" + "ab" * 32, image, "python", "-c", code,
    ], check=True, timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    args = parser.parse_args()
    violations = (scan_package(ROOT / "services/core-api/pymentor_core")
                  + scan_config(ROOT) + scan_retired_sources(ROOT))
    if violations:
        print(json.dumps({"violations": violations}))
        raise SystemExit(1)
    if args.image: check_image(args.image)
    print("PASS single PostgreSQL/Redis source/config gate")


if __name__ == "__main__": main()
