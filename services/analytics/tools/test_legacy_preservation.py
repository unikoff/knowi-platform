"""Prepare/run one isolated historical SQLite smoke. Default is an inert plan."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import threading
import time
from uuid import UUID

SERVICE = Path(__file__).resolve().parents[1]
WORKSPACE = SERVICE.parents[1]
CHECKOUT = Path(r"C:\Users\Unik\.codex\worktrees\knowi-prod-release\Pymentor_platform")
COMMIT = "4107334d8328b8d8b791322d67951d7ffb87890e"
EXPECTED = "95c70d6a338a8fd8222be9b1bdc9510146caa0cdb3d188fdbd7fdcad8e352964"
SIZE = 425984
ARCHIVES = (Path(r"C:\Project Python\_archives\Pymentor_platform-20261114-01a1da97"),
            Path(r"C:\Project Python\_archives\Pymentor_platform-monorepo-20261114-01a1da97"))
ORIGINAL = WORKSPACE / "services/core-api/.local/legacy/test.db"
RUN_ROOT = SERVICE / ".local/analytics-legacy-smoke"
RUNTIME = Path(__file__).parent / "legacy_preservation/runtime.py"
A2WSGI_CONSTRAINT = b"a2wsgi==1.10.10\n"
ALLOW = ("requirements.txt", "backend/requirements.txt", "backend/requirements-dual-local.txt",
         "backend/content/", "services/core-api/pymentor_core/", "services/core-api/Dockerfile")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.is_file():
        raise ValueError("unsafe source file")
    return path.stat()


def _manifest_entry(data):
    found = []
    def walk(value):
        if isinstance(value, dict):
            if value.get("path") == "backend/test.db":
                found.append(value)
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(data)
    if len(found) != 1 or found[0].get("sha256") != EXPECTED or found[0].get("bytes") != SIZE:
        raise ValueError("historical manifest mismatch")


def source_observations():
    for folder in ARCHIVES:
        manifest = folder / "snapshot.json"
        meta = _file(manifest)
        if meta.st_size > 8 * 1024 * 1024: raise ValueError("manifest too large")
        _manifest_entry(json.loads(manifest.read_text(encoding="utf-8-sig")))
    sources = (ORIGINAL, *(p / "before/backend/test.db" for p in ARCHIVES))
    result = []
    for source in sources:
        meta = _file(source)
        if meta.st_size != SIZE or sha(source) != EXPECTED:
            raise ValueError("historical SQLite mismatch")
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(source) + suffix).exists(): raise ValueError("SQLite sidecar requires reconciliation")
        result.append({"bytes": meta.st_size, "mtime_ns": meta.st_mtime_ns, "sha256": EXPECTED})
    return result


def verify_after(before):
    if source_observations() != before:
        raise ValueError("source metadata or bytes changed")


def call(args, *, timeout=60, env=None, input_data=None):
    try:
        result = subprocess.run(args, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, env=env, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("operation outcome unknown; reconcile") from None
    if result.returncode or len(result.stdout) > 40 * 1024 * 1024:
        raise RuntimeError("operation failed; reconcile")
    return result.stdout


def _safe_diagnostic(raw, operation):
    """No HTTP bodies, credential lines, URL queries or bearer material."""
    value = raw.decode("utf-8", errors="replace")
    if operation == "start":
        # The in-container harness deliberately emits only this public contract.
        # Unexpected output may contain private content and is never persisted.
        try:
            result = json.loads(value)
            if result == {"status": "FAIL"}:
                return b'{"status":"FAIL"}\n'
            if (set(result) == {"status", "phase", "error"} and result["status"] == "FAIL"
                    and result["phase"] in {"configuration", "dependencies", "service_start", "service_ready", "seed_copy", "login", "negative_auth", "admin_overview", "calendar", "summary", "admin_profile", "catalog", "progress"}
                    and result["error"] in {"runtime_error", "sqlite_error", "permission_denied", "read_only_filesystem", "missing_module", "database_open_failed", "missing_table", "server_error"}):
                return (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
            required = {"status", "http", "calendar", "summary", "admin", "progress", "negative_auth", "dependency_sha256"}
            if (set(result) == required and result["status"] == "PASS"
                    and all(result[k] is True for k in required - {"status", "dependency_sha256"})
                    and re.fullmatch(r"[0-9a-f]{64}", result["dependency_sha256"])):
                return (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
        except (ValueError, TypeError, KeyError):
            pass
        return b"[unrecognized container output suppressed]\n" if raw else b""
    lines = []
    for line in value.splitlines():
        if re.search(r"(?i)password|secret|authorization|bearer|token|credential|cookie|session|(?:https?://\S+\?)", line):
            lines.append("[sensitive diagnostic line suppressed]")
        else:
            lines.append(re.sub(r"(https?://)[^\s/@]+@", r"\1[redacted]@", line))
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")[:65536]


def docker_operation(folder, operation, args, *, timeout, env, spawn=subprocess.Popen):
    """One invocation, bounded private diagnostics, no retries after unknowns."""
    if operation not in {"build", "create", "start", "wait"}:
        raise ValueError("invalid Docker operation")
    began = time.monotonic()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    result, code, process = "not_started", None, None
    read_errors = []
    def read(stream, name):
        try:
            while True:
                part = stream.read(8192)
                if not part:
                    return
                totals[name] += len(part)
                buffers[name].extend(part[:max(0, 65536 - len(buffers[name]))])
        except Exception:
            read_errors.append(name)
    try:
        try:
            process = spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            raise RuntimeError("Docker command did not start; reconcile") from None
        result = "started_unknown"
        readers = [threading.Thread(target=read, args=(process.stdout, "stdout"), daemon=True),
                   threading.Thread(target=read, args=(process.stderr, "stderr"), daemon=True)]
        for thread in readers: thread.start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result = "timeout_unknown"
            process.kill()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: pass
            raise RuntimeError("Docker outcome unknown; reconcile") from None
        finally:
            for thread in readers: thread.join(timeout=10)
        if read_errors or any(thread.is_alive() for thread in readers):
            result = "diagnostic_incomplete_unknown"
            raise RuntimeError("Docker diagnostics incomplete; reconcile")
        result = "cli_success" if code == 0 else "cli_failed_reconcile"
        if code != 0:
            raise RuntimeError("Docker command failed; reconcile external state")
        # Build output is diagnostic only; its successful identity is checked
        # separately via iidfile. Create/start/wait output is protocol data.
        if operation != "build" and totals["stdout"] > 65536:
            result = "output_truncated_unknown"
            raise RuntimeError("Docker outcome requires reconciliation")
        return bytes(buffers["stdout"])
    finally:
        # stderr of start may originate from the runtime and is not a trusted
        # diagnostics format, so suppress arbitrary text as for stdout.
        for channel in ("stdout", "stderr"):
            safe_op = "start" if operation == "start" else operation
            write_new(folder / (operation + "." + channel + ".log"), _safe_diagnostic(bytes(buffers[channel]), safe_op))
        mark(folder, operation + "-diagnostics", {"outcome": result, "returncode": code if result != "timeout_unknown" else None,
             "seconds": round(time.monotonic() - began, 3), "stdout_bytes": totals["stdout"], "stderr_bytes": totals["stderr"],
             "stdout_truncated": totals["stdout"] > 65536, "stderr_truncated": totals["stderr"] > 65536})


def select_base_reference(tagged, expected_id):
    if tagged.get("Id") != expected_id:
        raise ValueError("frozen recipe base mismatch")
    references = {item for item in (tagged.get("RepoDigests") or []) if isinstance(item, str)
                  and re.fullmatch(r"(?:docker\.io/library/)?python@sha256:[0-9a-f]{64}", item)}
    if len({item.split("@", 1)[1] for item in references}) != 1:
        raise ValueError("local Python manifest digest unavailable or ambiguous")
    return sorted(references)[0]


def check_checkout():
    prefix = ["git", "-C", str(CHECKOUT)]
    if call(prefix + ["rev-parse", "HEAD"]).decode("ascii").strip() != COMMIT:
        raise ValueError("frozen checkout revision mismatch")
    if call(prefix + ["status", "--porcelain", "--untracked-files=all", "--", "requirements.txt", "backend", "services/core-api"]):
        raise ValueError("frozen checkout paths are not clean")


def extract_frozen(destination):
    archive = call(["git", "-C", str(CHECKOUT), "archive", "--format=tar", COMMIT, "--",
                    "requirements.txt", "backend/requirements.txt", "backend/requirements-dual-local.txt",
                    "backend/content", "services/core-api/pymentor_core", "services/core-api/Dockerfile"], timeout=60)
    if len(archive) > 32 * 1024 * 1024:
        raise ValueError("frozen source archive too large")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = tar.getmembers()
        if len(members) > 5000: raise ValueError("frozen source archive too large")
        for member in members:
            p = PurePosixPath(member.name)
            name = p.as_posix()
            if (p.is_absolute() or ".." in p.parts or not (member.isdir() or member.isfile())
                    or not any(name == allowed.rstrip("/") or name.startswith(allowed)
                               or (member.isdir() and allowed.startswith(name + "/")) for allowed in ALLOW)
                    or any(part.lower().startswith(".env") for part in p.parts)
                    or re.search(r"(?i)(\.db(?:-|$)|\.sqlite|\.pem$|\.key$)", name)):
                raise ValueError("unsafe frozen archive entry")
            output = destination.joinpath(*p.parts)
            if member.isdir(): output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                content = tar.extractfile(member).read()
                with output.open("xb") as target: target.write(content)
    recipe = destination / "services/core-api/Dockerfile"
    value = recipe.read_text(encoding="utf-8")
    old = "FROM python:3.12.16-slim-trixie"
    if value.count(old) != 2: raise ValueError("unexpected frozen image recipe")
    return value, recipe


def local_docker(docker, context, env, *, config_dir=None):
    prefix = [str(docker)] + (["--config", str(config_dir)] if config_dir is not None else [])
    result = json.loads(call(prefix + ["--context", context, "context", "inspect", context,
                              "--format", "{{json .Endpoints.docker.Host}}"], env=env).decode("utf-8"))
    if not isinstance(result, str) or not result.lower().startswith("npipe:////./pipe/"):
        raise ValueError("Docker context is not the local Windows pipe")
    return result


def validate_container(inspected, container, image, dbdata):
    host, mounts = inspected["HostConfig"], inspected.get("Mounts", [])
    if (inspected["Id"] != container or inspected["Image"] != image or host["NetworkMode"] != "none"
            or host.get("PortBindings") or host.get("PublishAllPorts") or host.get("ReadonlyRootfs") is not True
            or inspected["Config"].get("User") != "10001:10001"
            or host.get("Privileged") or host.get("CapAdd")
            or "ALL" not in (host.get("CapDrop") or [])
            or "no-new-privileges" not in " ".join(host.get("SecurityOpt") or [])
            or host.get("Memory") != 512 * 1024 * 1024 or host.get("NanoCpus") != 1_000_000_000
            or host.get("PidsLimit") != 128
            or len(mounts) != 1 or mounts[0].get("Destination") != "/data"
            or mounts[0].get("Type") != "bind" or mounts[0].get("RW") is not True
            or os.path.normcase(os.path.normpath(mounts[0].get("Source", ""))) != os.path.normcase(os.path.normpath(str(dbdata)))):
        raise ValueError("container isolation mismatch")


def validate_context(folder):
    """Fingerprint a bounded whitelist, refusing DB, credentials and links."""
    generated = {"smoke_runtime.py", "Smoke.Dockerfile", "legacy-constraints.txt"}
    digest, total, count = hashlib.sha256(), 0, 0
    for path in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix()):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("linked build context")
        name = path.relative_to(folder).as_posix()
        allowed = (name in generated or any(name == item.rstrip("/") or name.startswith(item)
                   or (path.is_dir() and item.startswith(name + "/")) for item in ALLOW))
        if (not allowed or any(part.lower().startswith(".env") for part in path.relative_to(folder).parts)
                or re.search(r"(?i)(\.db(?:-|$)|\.sqlite|\.pem$|\.key$)", name)):
            raise ValueError("unsafe build context")
        if path.is_file():
            data = path.read_bytes()
            count += 1
            total += len(data)
            if count > 5000 or total > 32 * 1024 * 1024:
                raise ValueError("build context exceeds bounds")
            key = name.encode("utf-8")
            digest.update(len(key).to_bytes(4, "big")); digest.update(key)
            digest.update(len(data).to_bytes(8, "big")); digest.update(data)
        elif not path.is_dir():
            raise ValueError("invalid build context entry")
    return digest.hexdigest()


def docker_environment(environ):
    # Do not expose host proxy, cloud, registry or other credential variables.
    safe = {"SYSTEMROOT", "WINDIR", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "PROGRAMDATA"}
    return {key: value for key, value in environ.items() if key.upper() in safe}


def build_command(docker_cmd, context_dir, iid, dependency_network):
    if dependency_network not in {"none", "default"}:
        raise ValueError("invalid dependency network")
    command = docker_cmd + ["build", "--pull=false", "--network=" + dependency_network,
                            "--iidfile", str(iid)]
    # Explicit empty values override proxy entries from Docker client config.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        command.extend(["--build-arg", key + "="])
    return command + ["--file", str(context_dir / "Smoke.Dockerfile"), str(context_dir)]


def write_new(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def mark(folder, name, payload):
    write_new(folder / (name + ".json"), json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def secure_private(folder):
    """Restrict this new run directory before copying any private bytes."""
    system_root = Path(os.environ["SystemRoot"]) / "System32"
    whoami = system_root / "whoami.exe"
    icacls = system_root / "icacls.exe"
    powershell = system_root / "WindowsPowerShell/v1.0/powershell.exe"
    output = call([str(whoami), "/user", "/fo", "csv", "/nh"]).decode("utf-8-sig", errors="strict")
    row = next(csv.reader(io.StringIO(output)))
    sid = row[-1]
    if not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", sid):
        raise ValueError("Windows user identity unavailable")
    call([str(icacls), str(folder), "/inheritance:r", "/grant:r",
          "*" + sid + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"])
    command = ("$ErrorActionPreference='Stop'; "
               "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
               "$a=Get-Acl -LiteralPath $env:LEGACY_SMOKE_ACL_PATH; "
               "$r=$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]); "
               "@{protected=$a.AreAccessRulesProtected;rules=@($r | ForEach-Object {"
               "@{sid=$_.IdentityReference.Value;allow=($_.AccessControlType -eq 'Allow');"
               "inherited=$_.IsInherited;rights=[int]$_.FileSystemRights;"
               "inheritance=[int]$_.InheritanceFlags}})} | ConvertTo-Json -Depth 5 -Compress")
    env = dict(os.environ, LEGACY_SMOKE_ACL_PATH=str(folder))
    acl = json.loads(call([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command], env=env).decode("utf-8-sig"))
    allowed = {sid, "S-1-5-18"}
    rules = acl.get("rules")
    if (acl.get("protected") is not True or not isinstance(rules, list) or not rules
            or any(rule.get("inherited") or (rule.get("allow") and rule.get("sid") not in allowed) for rule in rules)
            or any(not any(rule.get("sid") == ident and rule.get("allow") and
                               (rule.get("rights", 0) & 0x1F01FF) == 0x1F01FF and
                               (rule.get("inheritance", 0) & 3) == 3 for rule in rules) for ident in allowed)):
        raise ValueError("private Windows ACL unavailable")


def prepare_dir(run_id):
    for path in (SERVICE / ".local", RUN_ROOT):
        if path.exists() and (path.is_symlink() or path.resolve() != path.absolute()):
            raise ValueError("unsafe run root")
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    if RUN_ROOT.resolve() != RUN_ROOT.absolute(): raise ValueError("unsafe run root")
    folder = RUN_ROOT / run_id.hex
    folder.mkdir()
    secure_private(folder)
    return folder


def run_case(run_id, docker, docker_sha, context, base, dependency_network="none"):
    if os.environ.get("LEGACY_PRESERVATION_RUN_GO") != "1": raise ValueError("explicit run GO required")
    if os.name != "nt" or run_id.version != 4 or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", context):
        raise ValueError("invalid local run identity")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", base) or not re.fullmatch(r"[0-9a-f]{64}", docker_sha):
        raise ValueError("immutable executable and base image identities required")
    if dependency_network not in {"none", "default"}: raise ValueError("invalid dependency network")
    docker = Path(docker)
    if not docker.is_absolute() or sha(docker) != docker_sha: raise ValueError("Docker executable mismatch")
    _file(docker)
    before = source_observations()
    check_checkout()
    runtime_hash, runner_hash = sha(RUNTIME), sha(Path(__file__))
    folder = prepare_dir(run_id)
    mark(folder, "owner", {"run_id": str(run_id), "commit": COMMIT, "source_before": before,
                           "runtime_sha256": runtime_hash, "runner_sha256": runner_hash})
    env = docker_environment(os.environ)
    image, container = None, None
    try:
        endpoint = local_docker(docker, context, env)
        dbdata = folder / "data"
        dbdata.mkdir()
        (dbdata / "state").mkdir()
        copy = dbdata / "test.db"
        with ORIGINAL.open("rb") as source:
            write_new(copy, source.read(SIZE + 1))
        if copy.stat().st_size != SIZE or sha(copy) != EXPECTED: raise ValueError("copy mismatch")
        context_dir = folder / "context"
        context_dir.mkdir()
        recipe, frozen_recipe = extract_frozen(context_dir)
        if sha(RUNTIME) != runtime_hash or sha(Path(__file__)) != runner_hash: raise ValueError("harness changed")
        write_new(context_dir / "smoke_runtime.py", RUNTIME.read_bytes())
        write_new(context_dir / "legacy-constraints.txt", A2WSGI_CONSTRAINT)
        original_requirements = (context_dir / "requirements.txt").read_text(encoding="utf-8")
        if sum(line.strip() == "a2wsgi>=1.10" for line in original_requirements.splitlines()) != 1:
            raise ValueError("frozen dependency contract changed")
        requirement_lines = [line.strip() for line in original_requirements.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if any(line != "a2wsgi>=1.10" and not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", line) for line in requirement_lines):
            raise ValueError("unbounded frozen dependency")
        source_recipe_hash = sha(frozen_recipe)
        # Do not forward the user's Docker registry credentials or proxy config
        # to a network-enabled builder. Recreate only the verified local pipe.
        docker_config = folder / "docker-config"
        docker_config.mkdir()
        write_new(docker_config / "config.json", b'{"auths":{},"proxies":{}}')
        mark(folder, "context-pending", {"kind": "local-npipe"})
        call([str(docker), "--config", str(docker_config), "context", "create", "legacy-smoke",
              "--docker", "host=" + endpoint], env=env)
        if local_docker(docker, "legacy-smoke", env, config_dir=docker_config) != endpoint:
            raise ValueError("isolated Docker context mismatch")
        mark(folder, "context-confirmed", {"kind": "local-npipe"})
        docker_cmd = [str(docker), "--config", str(docker_config), "--context", "legacy-smoke"]
        info = json.loads(call(docker_cmd + ["image", "inspect", base], env=env))
        if len(info) != 1 or info[0].get("Id") != base: raise ValueError("base image mismatch")
        tagged = json.loads(call(docker_cmd + ["image", "inspect", "python:3.12.16-slim-trixie"], env=env))
        if len(tagged) != 1: raise ValueError("frozen recipe base mismatch")
        base_ref = select_base_reference(tagged[0], base)
        reference = json.loads(call(docker_cmd + ["image", "inspect", base_ref], env=env))
        if len(reference) != 1 or reference[0].get("Id") != base:
            raise ValueError("local manifest and base image mismatch")
        recipe = recipe.replace("FROM python:3.12.16-slim-trixie", "FROM " + base_ref)
        needle = "COPY requirements.txt ./requirements.txt"
        if recipe.count(needle) != 1: raise ValueError("frozen dependency recipe changed")
        recipe = recipe.replace(needle, needle + "\nCOPY legacy-constraints.txt /build/legacy-constraints.txt\n"
            "ENV PIP_CONSTRAINT=/build/legacy-constraints.txt PIP_CONFIG_FILE=/dev/null PIP_NO_INPUT=1 "
            "PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL= PIP_TRUSTED_HOST=")
        needle = 'RUN case "$PROFILE" in postgres_redis) pip install --no-cache-dir -r backend/requirements-dual-local.txt ;; legacy_sqlite) ;; *) exit 2 ;; esac'
        if recipe.count(needle) != 1: raise ValueError("frozen profile recipe changed")
        recipe = recipe.replace(needle, needle + "\nRUN python -c \"import importlib.metadata as m; print('\\n'.join(sorted('%s==%s' % (d.metadata['Name'],d.version) for d in m.distributions())))\" > /tmp/legacy-dependencies.txt")
        recipe += '\nCOPY --from=dependencies /tmp/legacy-dependencies.txt /legacy-dependencies.txt\nCOPY --from=dependencies /build/requirements.txt /legacy-requirements.txt\nCOPY --chown=app:app smoke_runtime.py /smoke_runtime.py\nENTRYPOINT ["python", "-B", "/smoke_runtime.py"]\nCMD []\n'
        write_new(context_dir / "Smoke.Dockerfile", recipe.encode())
        context_hash = validate_context(context_dir)
        build_metadata = {"base": base, "base_manifest": base_ref, "commit": COMMIT, "dependency_network": dependency_network,
                          "frozen_recipe_sha256": source_recipe_hash, "generated_recipe_sha256": sha(context_dir / "Smoke.Dockerfile"),
                          "requirements_sha256": sha(context_dir / "requirements.txt"),
                          "backend_requirements_sha256": sha(context_dir / "backend/requirements.txt"),
                          "dual_requirements_sha256": sha(context_dir / "backend/requirements-dual-local.txt"),
                          "constraint_sha256": sha(context_dir / "legacy-constraints.txt"), "context_sha256": context_hash}
        iid = folder / "image-id"
        if local_docker(docker, "legacy-smoke", env, config_dir=docker_config) != endpoint:
            raise ValueError("Docker endpoint changed")
        mark(folder, "build-pending", build_metadata)
        docker_operation(folder, "build", build_command(docker_cmd, context_dir, iid, dependency_network), env=env, timeout=600)
        if validate_context(context_dir) != context_hash: raise ValueError("build context changed")
        image = iid.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image): raise ValueError("image identity unavailable")
        mark(folder, "build-confirmed", {"image": image})
        if local_docker(docker, "legacy-smoke", env, config_dir=docker_config) != endpoint:
            raise ValueError("Docker endpoint changed")
        mark(folder, "create-pending", {"image": image})
        mount = f"type=bind,source={dbdata},target=/data"
        if "," in str(dbdata): raise ValueError("invalid bind path")
        raw = docker_operation(folder, "create", docker_cmd + ["create", "--name", "analytics-legacy-smoke-" + run_id.hex,
                   "--network", "none", "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
                   "--mount", mount, "--label", "knowi.legacy-smoke=" + run_id.hex,
                   "--env", "PYMENTOR_PROFILE=legacy_sqlite", "--env", "DATABASE_URL=sqlite:////data/test.db",
                   "--env", "PYTHON_DOTENV_DISABLED=1", "--env", "PYMENTOR_LOCAL_NOTIFICATIONS=0",
                   "--env", "PYMENTOR_STATE_DIR=/data/state", image], env=env, timeout=30).decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", raw): raise ValueError("container identity unavailable")
        container = raw
        inspected = json.loads(call(docker_cmd + ["inspect", "--type", "container", container], env=env))[0]
        validate_container(inspected, container, image, dbdata)
        mark(folder, "create-confirmed", {"container": container})
        if local_docker(docker, "legacy-smoke", env, config_dir=docker_config) != endpoint:
            raise ValueError("Docker endpoint changed")
        mark(folder, "start-pending", {"container": container})
        raw = docker_operation(folder, "start", docker_cmd + ["start", "--attach", container], env=env, timeout=120)
        mark(folder, "wait-pending", {"container": container})
        exit_value = docker_operation(folder, "wait", docker_cmd + ["wait", container], env=env, timeout=15)
        if exit_value.strip() != b"0":
            raise ValueError("container exit not successful")
        mark(folder, "wait-confirmed", {"container": container, "exit": 0})
        result = json.loads(raw.decode("utf-8"))
        expected_result = {"status": "PASS", "http": True, "calendar": True, "summary": True,
                           "admin": True, "progress": True, "negative_auth": True}
        if (not isinstance(result, dict) or set(result) != set(expected_result) | {"dependency_sha256"}
                or {key: result[key] for key in expected_result} != expected_result
                or not re.fullmatch(r"[0-9a-f]{64}", result["dependency_sha256"])):
            raise ValueError("smoke result unavailable")
        expected_result = result
        mark(folder, "start-confirmed", {"container": container, "exit": 0})
        if sha(RUNTIME) != runtime_hash or sha(Path(__file__)) != runner_hash: raise ValueError("harness changed")
        verify_after(before)
    finally:
        # No automatic deletion or replay: an unknown Docker outcome and the
        # owned copy are left for exact-identity reconciliation.
        after = source_observations()
        mark(folder, "source-after", {"source_after": after, "unchanged": after == before})
        if after != before: raise ValueError("source changed")
    mark(folder, "completed", {"result": expected_result, "source_after": before, "image": image, "container": container})
    return expected_result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--docker")
    parser.add_argument("--docker-sha256")
    parser.add_argument("--context")
    parser.add_argument("--base-image-id")
    parser.add_argument("--dependency-network", choices=("none", "default"), default="none")
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"status": "UNRUN", "commit": COMMIT, "scope": "copy-only frozen HTTP smoke"}))
        return 0
    if any(getattr(args, key) is None for key in ("run_id", "docker", "docker_sha256", "context", "base_image_id")):
        raise ValueError("explicit identities required")
    run_id = UUID(args.run_id)
    if str(run_id) != args.run_id: raise ValueError("canonical run identity required")
    print(json.dumps(run_case(run_id, args.docker, args.docker_sha256, args.context, args.base_image_id, args.dependency_network)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Legacy smoke stopped or outcome unknown; preserve state and reconcile.", file=sys.stderr)
        raise SystemExit(2) from None
