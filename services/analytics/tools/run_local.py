"""Operate only the explicitly named synthetic application Compose project."""
import argparse
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import bootstrap_postgres as db
import bootstrap_runtime as runtime
import check_k3s
from pydantic import SecretStr
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymentor_analytics.adapters.auth.config import load_manifest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
COMPOSE_DIR = REPO / "infra" / "analytics-local"
PROJECT = "knowi-analytics-app-01a1df75"
SERVICES = {"collector", "processor", "query", "migrate", "ingress"}
DEFAULT_IMAGE = "knowi-analytics:an1-current"
MANIFEST = ROOT / ".local" / "trust-manifest.json"
TRUST_OVERRIDE = ROOT / ".local" / "trust-override.json"
TRUST_FIELDS = ("GRANT_ISSUER", "GRANT_KEYS_JSON", "PRODUCER_ISSUER", "PRODUCER_KEYS_JSON",
                "PRODUCER_ALLOWLIST_JSON", "TRUST_MANIFEST_JSON")
PROBE_SCRIPT = ("import urllib.request,urllib.error; "
                "exec(\"for p in ('live','ready'):\\n try:\\n  r=urllib.request.urlopen('http://127.0.0.1:8000/'+p,timeout=2); print(p,r.status)\\n except urllib.error.HTTPError as e:\\n  print(p,e.code)\")")
TRUST_PROBE_SCRIPT = ("import urllib.request,urllib.error; "
                      "exec(\"try:\\n r=urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=2)\\n except urllib.error.HTTPError as e:\\n r=e\\n print(r.headers.get('X-Analytics-Trust-Fingerprint',''))\")")


def trust_manifest():
    if "ANALYTICS_TRUST_MANIFEST_JSON" in os.environ or any(
        f"ANALYTICS_{role}_{field}" in os.environ
        for role in ("COLLECTOR", "PROCESSOR", "QUERY") for field in TRUST_FIELDS
    ):
        raise ValueError("conflicting trust environment")
    if MANIFEST.is_symlink() or MANIFEST.parent.is_symlink():
        raise ValueError("unsafe manifest path")
    if not MANIFEST.exists():
        return None
    if MANIFEST.stat().st_size > 128 * 1024:
        raise ValueError("manifest too large")
    raw = MANIFEST.read_text(encoding="utf-8")
    _, _, fingerprint = load_manifest(SecretStr(raw))
    return raw, fingerprint


def trust_override_document(manifest):
    value = manifest.replace("$", "$$")  # Compose interpolation must preserve the source value.
    return {"x-analytics-local-generated": {"version": 1}, "services": {
        role: {"environment": {"ANALYTICS_TRUST_MANIFEST_JSON": value}}
        for role in ("collector", "processor", "query")}}


def write_trust_override(manifest):
    if TRUST_OVERRIDE.is_symlink() or TRUST_OVERRIDE.parent.is_symlink():
        raise ValueError("unsafe trust override path")
    if TRUST_OVERRIDE.exists():
        existing = json.loads(TRUST_OVERRIDE.read_text(encoding="utf-8"))
        if existing.get("x-analytics-local-generated") != {"version": 1}:
            raise ValueError("trust override is not managed by this tool")
    document = trust_override_document(manifest)
    temporary = TRUST_OVERRIDE.with_name(TRUST_OVERRIDE.name + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(document, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, TRUST_OVERRIDE)
    return TRUST_OVERRIDE


def docker_url(url):
    parsed = urlsplit(url)
    if parsed.hostname != "127.0.0.1" or parsed.port != 57392:
        raise ValueError("unsafe DB URL")
    credentials = parsed.netloc.rsplit("@", 1)[0]
    return urlunsplit((parsed.scheme, credentials + "@analytics-postgres:5432", parsed.path, "", ""))


def compose_env(record):
    runtime.validate_record(record)
    if record["state"] != "ready":
        raise ValueError("database migration is unresolved")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("COMPOSE_") or key.startswith("ANALYTICS_"):
            del env[key]
    for role in ("collector", "processor", "query", "migrate"):
        env[f"ANALYTICS_{role.upper()}_DATABASE_URL"] = docker_url(record["urls"][role])
    env["ANALYTICS_IMAGE"] = os.environ.get("ANALYTICS_IMAGE", DEFAULT_IMAGE)
    return env


def inspection_env():
    """Compose needs interpolation for ps/stop; no real credentials are needed."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("COMPOSE_") and not k.startswith("ANALYTICS_")}
    for role in ("collector", "processor", "query", "migrate"):
        env[f"ANALYTICS_{role.upper()}_DATABASE_URL"] = "postgresql://disabled@127.0.0.1:57392/analytics_test_placeholder"
    env["ANALYTICS_IMAGE"] = DEFAULT_IMAGE
    return env


def docker(args, *, env=None, timeout=45):
    result = subprocess.run(["docker", *args], env=env, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("local Docker operation failed")
    return result.stdout


def project_containers():
    ids = docker(["ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"]).splitlines()
    if not ids:
        return []
    containers = json.loads(docker(["inspect", *ids]))
    for container in containers:
        labels = container["Config"].get("Labels") or {}
        workdir = labels.get("com.docker.compose.project.working_dir", "")
        if (labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.service") not in SERVICES
                or Path(workdir).resolve() != COMPOSE_DIR.resolve()):
            raise ValueError("unexpected container in application project")
    return containers


def verify_project():
    project_containers()


def verify_replay_retirement(record):
    """Require the owned old readers/workers and their DB sessions to be gone."""
    runtime.validate_record(record)
    if record["state"] != "ready":
        raise ValueError("runtime is not initialized")
    check_k3s.local_docker_env()
    for container in project_containers():
        role = container["Config"]["Labels"]["com.docker.compose.service"]
        if role in {"processor", "query"}:
            state = container.get("State") or {}
            if state.get("Running") is not False or state.get("Status") not in {"created", "exited"}:
                raise ValueError("old processor or query is not stopped")
    roles = tuple(f"{record['database']}_{role}" for role in ("processor", "query"))
    with db.connect(db.config(runtime.CONNECTION)) as conn:
        visible = conn.execute("SELECT rolsuper OR pg_has_role(current_user, 'pg_read_all_stats', 'MEMBER') "
                               "FROM pg_roles WHERE rolname = current_user").fetchone()
        if visible != (True,):
            raise ValueError("database session visibility is insufficient")
        row = conn.execute("SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usename IN (%s, %s)", roles).fetchone()
    if row != (0,):
        raise ValueError("processor or query database sessions remain")


def runtime_files_fingerprint(root):
    """Hash relative names and bytes identically on the host and inside image."""
    import hashlib
    files = [root / "requirements-runtime.txt", root / "alembic.ini"]
    files.extend((root / "pymentor_analytics").rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


IMAGE_HISTORY_PROBE = '''
import hashlib, json, signal
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
if hasattr(signal, "alarm"):
    signal.alarm(15)
''' + inspect.getsource(runtime_files_fingerprint) + '''
root = Path("/app")
scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
heads = scripts.get_heads()
if len(heads) != 1:
    raise ValueError("expected one head")
revisions = list(scripts.walk_revisions())
if not revisions or revisions[0].revision != heads[0]:
    raise ValueError("invalid history")
digest = hashlib.sha256()
for index, revision in enumerate(revisions):
    expected = revisions[index + 1].revision if index + 1 < len(revisions) else None
    if revision.down_revision != expected or revision.dependencies:
        raise ValueError("nonlinear history")
    digest.update(revision.revision.encode("utf-8") + b"\\0")
    digest.update(Path(revision.path).read_bytes())
print(json.dumps({"history": [heads[0], [r.revision for r in revisions], digest.hexdigest()],
                  "runtime_sha256": runtime_files_fingerprint(root)}, separators=(",", ":")))
'''


def resolve_verified_image(image_ref, expected_history, expected_runtime=None):
    """Return an immutable local image ID only after isolated source comparison."""
    if not isinstance(image_ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}", image_ref):
        raise ValueError("invalid image reference")
    check_k3s.local_docker_env()
    image_id = docker(["image", "inspect", "--format", "{{.Id}}", image_ref], timeout=10).strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("image ID is not immutable")
    expected_runtime = expected_runtime or runtime_files_fingerprint(ROOT)
    if check_k3s.available_memory() < 640 * 1024**2:
        raise ValueError("insufficient memory for image probe")
    token = uuid4().hex
    name = "knowi-analytics-imagecheck-" + token
    args = ["run", "--rm", "--pull=never", "--name", name,
            "--label", f"knowi.analytics.imagecheck={token}", "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=128m", "--cpus=0.25",
            "--pids-limit=64", "--user=10001", "--workdir=/app", "--entrypoint=python",
            image_id, "-c", IMAGE_HISTORY_PROBE]
    try:
        output = docker(args, timeout=25)
    except subprocess.TimeoutExpired:
        # Only remove our uniquely named probe after its image and nonce match.
        try:
            identity = docker(["inspect", "--format", '{{.Image}} {{index .Config.Labels "knowi.analytics.imagecheck"}}', name], timeout=10).strip()
            if identity != f"{image_id} {token}":
                raise ValueError("image probe identity mismatch")
            docker(["rm", "--force", name], timeout=10)
        except Exception:
            raise RuntimeError("image probe cleanup outcome unknown") from None
        raise RuntimeError("image probe timed out") from None
    result = json.loads(output)
    expected = [expected_history[0], list(expected_history[1]), expected_history[2]]
    if (not isinstance(result, dict) or set(result) != {"history", "runtime_sha256"}
            or result["history"] != expected or result["runtime_sha256"] != expected_runtime):
        raise ValueError("image does not match current source")
    return image_id


def compose(args, env, *, override=None, timeout=120):
    files = ["-f", str(COMPOSE_DIR / "compose.yaml")]
    if override is not None:
        files.extend(["-f", str(override)])
    return docker(["compose", "-p", PROJECT, *files, *args], env=env, timeout=timeout)


def compose_rows(output):
    if not output.strip():
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return [json.loads(line) for line in output.splitlines() if line.strip()]
    return parsed if isinstance(parsed, list) else [parsed]


def required_app_memory(config):
    services = config.get("services", {})
    caps = {"collector": 256, "processor": 256, "query": 256, "ingress": 128}
    if set(services) != set(caps):
        raise ValueError("unexpected application services")
    total = 0
    for name, max_mib in caps.items():
        value = int(services[name].get("mem_limit", 0))
        if not 0 < value <= max_mib * 1024**2:
            raise ValueError("application memory limit missing or unsafe")
        total += value
    # 358 MiB PG headroom, 512 MiB host reserve; round up to 256 MiB.
    total += (358 + 512) * 1024**2
    quantum = 256 * 1024**2
    return ((total + quantum - 1) // quantum) * quantum


def verify_running_trust(env, override, expected, *, timeout=15):
    deadline = time.monotonic() + timeout
    for role in ("collector", "query"):
        while True:
            try:
                value = compose(["exec", "-T", role, "python", "-c", TRUST_PROBE_SCRIPT], env,
                                override=override, timeout=5).strip()
                if value == expected:
                    break
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
            if time.monotonic() >= deadline:
                raise ValueError("running trust fingerprint not verified")
            time.sleep(0.2)


def start():
    with runtime.operation_lock():
        record = runtime.read_record()
        if record["state"] != "ready":
            raise ValueError("migration outcome unresolved")
        history = runtime.schema_history()
        revision = runtime.current_revision(runtime.verify_database(db.config(runtime.CONNECTION), record), history[1])
        if revision != history[0]:
            raise ValueError("explicit schema upgrade required")
        manifest = trust_manifest()
        env = compose_env(record)
        check_k3s.local_docker_env()
        verify_project()
        source_fingerprint = runtime_files_fingerprint(ROOT)
        env["ANALYTICS_IMAGE"] = resolve_verified_image(env["ANALYTICS_IMAGE"], history, source_fingerprint)
        override = write_trust_override(manifest[0]) if manifest is not None else None
        config = json.loads(compose(["config", "--format", "json"], env, override=override))
        required = required_app_memory(config)
        if check_k3s.available_memory() < required:
            raise ValueError("insufficient host memory to start containers")
        if runtime.schema_history() != history or runtime_files_fingerprint(ROOT) != source_fingerprint:
            raise ValueError("source changed before startup")
        compose(["up", "-d", "--no-build"], env, override=override)
        try:
            if runtime.schema_history() != history or runtime_files_fingerprint(ROOT) != source_fingerprint:
                raise ValueError("source changed during startup")
            if manifest is not None:
                verify_running_trust(env, override, manifest[1])
                current = trust_manifest()
                if current is None or current[1] != manifest[1]:
                    raise ValueError("trust manifest changed during startup")
        except Exception:
            # The project is isolated; a failed verification must not leave it serving.
            compose(["stop"], env, override=override)
            raise
        if manifest is not None:
            print("Application started; both running trust fingerprints matched. Privacy readiness remains unverified.")
        else:
            print("Application started without a trust manifest; authentication remains unconfigured.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "upgrade", "start", "status", "stop"))
    action = parser.parse_args().action
    if action in {"init", "upgrade"}:
        with runtime.operation_lock():
            runtime.init() if action == "init" else runtime.upgrade()
        print("Synthetic schema initialized or reconciled; privacy readiness is separate.")
        return
    if action == "start":
        start()
        return
    env = inspection_env()
    check_k3s.local_docker_env()
    verify_project()
    if action == "stop":
        compose(["stop"], env)
        print("Application containers stopped; database and volumes retained.")
    else:
        output = compose(["ps", "--all", "--format", "json"], env)
        rows = compose_rows(output)
        for row in rows:
            if row.get("Service") not in SERVICES:
                raise ValueError("unexpected service")
            print(f"{row['Service']}: {row.get('State', 'unknown')}")
            if row["Service"] in {"collector", "processor", "query"} and row.get("State") == "running":
                try:
                    result = compose(["exec", "-T", row["Service"], "python", "-c", PROBE_SCRIPT], env)
                    for line in result.splitlines():
                        if line in {"live 200", "ready 200", "ready 503"}:
                            print(f"  {line}")
                except (RuntimeError, subprocess.TimeoutExpired):
                    print("  probes unavailable")
        print("Runtime state only; readiness and real-data use are not established.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Operation refused or failed; verify local target and runtime metadata.", file=sys.stderr)
        raise SystemExit(2) from None
