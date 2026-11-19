"""Owned disposable resources for an explicitly authorized local rehearsal.

No database connection or process is opened on import. On errors resources are
left in place for reconciliation; this module never guesses an unknown outcome.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from .guards import ANALYTICS_NAME, CORE_NAME

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT.parent / "core-api"
ROLES = ("migrate", "collector", "processor", "query", "privacy", "transfer_status")
_JOB = None


def set_job(job):
    global _JOB
    _JOB = job


def available_memory_mib():
    import ctypes
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in ("total", "available", "total_page", "available_page", "total_virtual", "available_virtual", "available_extended")]
    state = Memory()
    state.length = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise OSError("cannot measure memory headroom")
    return state.available // (1024 * 1024)


def core_admin_url(value):
    p = urlsplit(value)
    if (p.scheme != "postgresql" or p.hostname != "127.0.0.1" or p.port != 57382
            or p.username != "pymentor_test" or p.path != "/pymentor_test" or p.query or p.fragment):
        raise ValueError("Core bootstrap target is not the synthetic allowlist")
    return p


def private_folder():
    project = ROOT.resolve(strict=True)
    local = (ROOT / ".local").resolve()
    root = (local / "cross-service").resolve()
    if local.parent != project or root.parent != local:
        raise ValueError("private root must stay inside Analytics workspace")
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid4().hex
    # On Windows the sandbox can make mkdir(mode=0o700) inaccessible even to
    # the creator. Inherit the private .local directory's Windows ACL instead.
    path.mkdir()
    if path.resolve(strict=True).parent != root:
        raise ValueError("private fixture directory escaped root")
    token = secrets.token_hex(32)
    write_new(path / "owner.json", {"token": token, "state": "created", "processes": [], "resources": []})
    return path, token


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())


def record(path, token, **updates):
    file = path / "owner.json"
    state = json.loads(file.read_text(encoding="utf-8"))
    if state["token"] != token:
        raise ValueError("private owner sentinel mismatch")
    state.update(updates)
    temporary = path / ("record-" + uuid4().hex + ".tmp")
    write_new(temporary, state)
    os.replace(temporary, file)


def create_core(admin_dsn, folder, token):
    import psycopg
    from psycopg import sql
    parsed = core_admin_url(admin_dsn)
    name = "analytics_delivery_test_" + uuid4().hex
    sentinel = secrets.token_hex(32)
    install_sentinel = uuid4()
    target = urlunsplit((parsed.scheme, parsed.netloc, "/" + name, "", ""))
    record(folder, token, state="core_create_pending", core_name=name, core_sentinel=sentinel,
           core_install_sentinel=str(install_sentinel))
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3) as conn:
        actual = conn.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int").fetchone()
        if actual[:2] != ("pymentor_test", "pymentor_test") or not 160000 <= actual[2] < 170000:
            raise ValueError("Core bootstrap server identity mismatch")
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER pymentor_test").format(sql.Identifier(name)))
    with psycopg.connect(target, autocommit=True, connect_timeout=3) as conn:
        conn.execute("CREATE TABLE public.cross_service_sentinel (token text NOT NULL)")
        conn.execute("INSERT INTO public.cross_service_sentinel VALUES (%s)", (sentinel,))
        conn.execute("REVOKE ALL ON public.cross_service_sentinel FROM PUBLIC")
        conn.execute("CREATE TABLE public.analytics_delivery_install_sentinel (token uuid NOT NULL)")
        conn.execute("INSERT INTO public.analytics_delivery_install_sentinel VALUES (%s)", (install_sentinel,))
        conn.execute("REVOKE ALL ON public.analytics_delivery_install_sentinel FROM PUBLIC")
    record(folder, token, state="core_created")
    return {"name": name, "owner": "pymentor_test", "sentinel": sentinel, "dsn": target}


def create_analytics(folder, token, *, benchmark_postgres=False):
    from psycopg import sql
    from tools.bootstrap_postgres import config, connect
    if type(benchmark_postgres) is not bool: raise ValueError("invalid benchmark target")
    data = (config(ROOT / ".local/benchmark-wsl-postgres.json", benchmark=True) if benchmark_postgres
            else config(ROOT / ".local/postgres-connection.json"))
    name, sentinel = "analytics_test_" + uuid4().hex, secrets.token_hex(32)
    passwords = {role: secrets.token_urlsafe(32) for role in ROLES}
    port = data["port"] if benchmark_postgres else 57392
    urls = {role: f"postgresql://{name}_{role}:{quote(password, safe='')}@127.0.0.1:{port}/{name}"
            for role, password in passwords.items()}
    # The exact names are persisted before any side effect, including partial
    # failures. Automatic cleanup is forbidden until creation is reconciled.
    record(folder, token, state="analytics_create_pending", analytics_name=name, analytics_sentinel=sentinel,
           analytics_target="benchmark_wsl" if benchmark_postgres else "default", analytics_port=port)
    with (connect(data, benchmark=True) if benchmark_postgres else connect(data)) as conn:
        for role in ROLES:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS")
                         .format(sql.Identifier(name + "_" + role), sql.Literal(passwords[role])))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(name + "_migrate")))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        for role in ROLES:
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(name + "_" + role)))
    with (connect(data, name, benchmark=True) if benchmark_postgres else connect(data, name)) as conn:
        conn.execute("CREATE TABLE public.analytics_test_sentinel (token text NOT NULL)")
        conn.execute("INSERT INTO public.analytics_test_sentinel VALUES (%s)", (sentinel,))
        conn.execute("REVOKE ALL ON public.analytics_test_sentinel FROM PUBLIC")
        conn.execute("CREATE TABLE public.cross_service_sentinel (token text NOT NULL)")
        conn.execute("INSERT INTO public.cross_service_sentinel VALUES (%s)", (sentinel,))
        conn.execute("REVOKE ALL ON public.cross_service_sentinel FROM PUBLIC")
        for role in ROLES:
            conn.execute(sql.SQL("GRANT SELECT ON public.cross_service_sentinel TO {}").format(sql.Identifier(name + "_" + role)))
    target = {"name": name, "owner": name + "_migrate", "sentinel": sentinel, "dsn": urls["migrate"]}
    state = json.loads((folder / "owner.json").read_text(encoding="utf-8"))
    owned = state.get("analytics_resources", []) + [{"name": name, "sentinel": sentinel}]
    record(folder, token, state="analytics_status_created", analytics_resources=owned)
    return target, urls, data


def install_analytics(urls, *, full_chain=False, activity_projection=False, day_projection=False):
    from pymentor_analytics.adapters.postgres.migrate import upgrade
    from pymentor_analytics.app.config import Settings
    if activity_projection and not full_chain:
        raise ValueError("activity projection requires the full preceding chain")
    if day_projection and not (full_chain and activity_projection):
        raise ValueError("day projection requires the full a010 chain")
    revision = "a011" if day_projection else ("a010" if activity_projection else ("a009" if full_chain else "a007"))
    upgrade(Settings(database_url=urls["migrate"]), target_revision=revision)


def verify_analytics_grants(target):
    import psycopg
    name = target["name"]
    with psycopg.connect(target["dsn"], connect_timeout=3, options="-c statement_timeout=3000 -c default_transaction_read_only=on") as conn:
        attrs = conn.execute("SELECT rolsuper,rolinherit,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname=%s",
                             (name + "_transfer_status",)).fetchone()
        if attrs != (False, False, False, False, False, True):
            raise ValueError("transfer status role attributes invalid")
        status = name + "_transfer_status"
        actual = conn.execute("""SELECT
          has_column_privilege(%s,'analytics.activity_facts','subject_ref','SELECT'),
          has_column_privilege(%s,'analytics.activity_facts','accepted_event','SELECT'),
          has_table_privilege(%s,'analytics.activity_facts','INSERT'),
          has_table_privilege(%s,'analytics.core_transfer_projection','DELETE'),
          has_table_privilege(%s,'analytics.core_transfer_entities','INSERT'),
          has_table_privilege(%s,'analytics.core_transfer_projection','UPDATE'),
          has_table_privilege(%s,'analytics.core_transfer_projection','SELECT'),
          has_table_privilege(%s,'analytics.core_transfer_projection','DELETE')""",
          (status, status, status, status, name + "_collector", name + "_processor", name + "_query", name + "_privacy")).fetchone()
        if actual != (True, False, False, False, True, True, True, True):
            raise ValueError("Analytics runtime role grants differ from expected boundary")


def run_child(args, *, cwd, env, timeout=120):
    if _JOB is None:
        raise RuntimeError("owned process job must be installed first")
    result = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        _JOB.assign(result)
        stdout, stderr = result.communicate(timeout=timeout)
    except BaseException:
        if result.poll() is None:
            result.kill()
            result.wait(timeout=5)
        raise
    if result.returncode:
        private = env.get("CROSS_SERVICE_PRIVATE_DIR")
        if private:
            folder = Path(private).resolve(strict=True)
            if folder.parent == (ROOT / ".local/cross-service").resolve(strict=True):
                with (folder / ("child-error-" + uuid4().hex + ".log")).open("xb") as output:
                    output.write(stdout[:65536] + b"\n" + stderr[:65536])
        raise RuntimeError("fixture child failed; inspect owned private state")


def local_ports_free(ports):
    held = []
    try:
        for port in ports:
            sock = socket.socket()
            held.append(sock)
            sock.bind(("127.0.0.1", port))
    finally:
        for sock in held:
            sock.close()


def unused_port():
    for _ in range(10):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in range(18151, 18158):
            return port
    raise RuntimeError("could not choose an independent loopback port")


class OwnedJob:
    """Windows job closes all child processes if the harness itself exits."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("owned process lifecycle requires Windows")
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "cannot create owned process job")
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error(); kernel.CloseHandle(handle)
            raise OSError(error, "cannot set owned process job limits")
        self._kernel, self._handle = kernel, handle

    def assign(self, process):
        if not self._kernel.AssignProcessToJobObject(self._handle, int(process._handle)):
            import ctypes
            raise OSError(ctypes.get_last_error(), "cannot assign owned child to job")

    def assert_listener(self, port):
        import ctypes
        from ctypes import wintypes
        executable = str(Path(os.environ["SystemRoot"]) / "System32/netstat.exe")
        result = subprocess.run([executable, "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=5, check=True)
        matching = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "TCP" and parts[1] == f"127.0.0.1:{port}" and parts[-2] == "LISTENING":
                matching.append(int(parts[-1]))
        if len(matching) != 1:
            raise RuntimeError("loopback listener identity unavailable")
        self._kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel.OpenProcess.restype = wintypes.HANDLE
        self._kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self._kernel.IsProcessInJob.restype = wintypes.BOOL
        process = self._kernel.OpenProcess(0x1000, False, matching[0])
        if not process:
            raise OSError(ctypes.get_last_error(), "cannot identify listener owner")
        try:
            member = wintypes.BOOL()
            if not self._kernel.IsProcessInJob(process, self._handle, ctypes.byref(member)) or not member.value:
                raise RuntimeError("listener is not owned by this fixture job")
        finally:
            self._kernel.CloseHandle(process)

    def close(self):
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def spawn_owned(args, *, cwd, env, folder, label, children, job, on_spawn):
    log = (folder / (label + ".log")).open("xb")
    try:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        log.close()
    try:
        job.assign(process)
        children.append(process)
        on_spawn(children)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise
    return process


def wait_http(process, url, *, timeout=30):
    import httpx
    until = time.monotonic() + timeout
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < until:
            if process.poll() is not None:
                raise RuntimeError("owned process exited before readiness")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.1)
    raise TimeoutError("owned process readiness deadline exceeded")


def wait_port(process, port, *, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError("owned proxy exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise TimeoutError("owned proxy readiness deadline exceeded")


def processes_stop(children):
    # Popen handles bind these signals to children launched by this process.
    for child in reversed(children):
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
    for child in reversed(children):
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def cleanup_core(admin_dsn, target):
    import psycopg
    from psycopg import sql
    core_admin_url(admin_dsn)
    if not CORE_NAME.fullmatch(target["name"]):
        raise ValueError("unsafe Core database name")
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        identity = conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        sentinel = conn.execute("SELECT token FROM public.cross_service_sentinel").fetchall()
        if identity != (target["name"], "pymentor_test") or sentinel != [(target["sentinel"],)]:
            raise ValueError("Core cleanup sentinel or owner mismatch")
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3) as conn:
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target["name"])))


def cleanup_analytics(data, target):
    from psycopg import sql
    from tools.bootstrap_postgres import cleanup, connect
    if not ANALYTICS_NAME.fullmatch(target["name"]):
        raise ValueError("unsafe Analytics database name")
    # Bootstrap checks its own sentinel and DB owner before dropping either.
    cleanup(data, target["name"], target["sentinel"])
    with connect(data) as conn:
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(target["name"] + "_transfer_status")))


def cleanup_private(folder, token):
    """Remove only the owned fixture; retain its sentinel until all other entries are gone."""
    root = (ROOT / ".local/cross-service").resolve(strict=True)
    folder = folder.resolve(strict=True)
    if folder.parent != root:
        raise ValueError("private cleanup path mismatch")
    owner = folder / "owner.json"
    raw_owner = owner.read_bytes()
    if json.loads(raw_owner)["token"] != token:
        raise ValueError("private cleanup owner mismatch")
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in directories + files:
            item = Path(current) / name
            if item.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(item)):
                raise ValueError("private cleanup refuses links")
    for child in folder.iterdir():
        if child == owner:
            continue
        for attempt in range(3):
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(.25 * (attempt + 1))
    owner.unlink()
    try:
        folder.rmdir()
    except OSError:
        # Preserve the marker if Windows briefly retains an empty directory.
        if folder.is_dir() and not owner.exists():
            fd = os.open(owner, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw_owner)
        raise
