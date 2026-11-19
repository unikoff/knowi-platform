"""Guarded identity and lifecycle for the separate synthetic K3s database.

No action runs on import. Creation and the Kubernetes migration Job require
separate authorization; a pending operation is never automatically repeated.
"""
import ipaddress
import json
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import bootstrap_postgres as db
import bootstrap_runtime as runtime
import resource_snapshot as docker

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
RECORD = LOCAL / "k3s-runtime.json"
LOCK = LOCAL / "k3s-database.operation.lock"
CONNECTION = LOCAL / "postgres-connection.json"
PROJECT = "knowi-analytics-01a1df75-k3s"
NETWORK = "knowi-analytics-01a1df75-net"
TARGET = "a006"


def _unsafe(path):
    return path.is_symlink() or path.is_junction()


def _paths():
    if _unsafe(LOCAL) or not LOCAL.is_dir():
        raise ValueError("unsafe local directory")
    if any(_unsafe(path) for path in (RECORD, LOCK, CONNECTION)):
        raise ValueError("unsafe metadata path")


@contextmanager
def _lock():
    _paths()
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        yield
    finally:
        LOCK.unlink()


def _history():
    # The preserved v2 fixture remains pinned to a006 even after a newer
    # migration chain is promoted; its record must not silently adopt head.
    history = runtime.schema_history()
    if history[0] != TARGET:
        history = runtime.schema_history(TARGET)
    if history[0] != TARGET:
        raise ValueError("unexpected migration head")
    return history


def _write(record, *, exclusive=False):
    _paths()
    temp = RECORD if exclusive else LOCAL / "k3s-runtime.json.pending"
    if _unsafe(temp):
        raise ValueError("unsafe metadata path")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(record, out, separators=(",", ":"))
        out.flush()
        os.fsync(out.fileno())
    if not exclusive:
        os.replace(temp, RECORD)


def _validate(record, history):
    if not isinstance(record, dict) or record.get("project") != PROJECT:
        raise ValueError("invalid K3s metadata")
    state = record.get("state")
    if state not in {"created", "migration_pending", "migration_issued", "ready"}:
        raise ValueError("incomplete provisioning; manual reconciliation required")
    expected = {"synthetic_only", "do_not_drop", "project", "state", "database", "nonce",
                "urls", "revision", "history_sha256"}
    if state in {"migration_pending", "migration_issued"}:
        expected.add("target_revision")
    if set(record) != expected or record.get("history_sha256") != history[2]:
        raise ValueError("unknown metadata or changed history")
    revision = record.get("revision")
    if revision is not None and revision not in history[1]:
        raise ValueError("unknown database revision")
    if state == "created" and revision is not None:
        raise ValueError("invalid created state")
    if state == "ready" and revision is None:
        raise ValueError("invalid ready state")
    if state in {"migration_pending", "migration_issued"} and record.get("target_revision") != TARGET:
        raise ValueError("invalid migration target")
    # Reuse the existing strict role URLs and synthetic identity validation.
    mapped = {**record, "project": runtime.PROJECT,
              "state": "migration_pending" if state in {"migration_pending", "migration_issued"} else "ready"}
    runtime.validate_record(mapped)
    return record


def _read(history):
    _paths()
    if RECORD.stat().st_size > 16384:
        raise ValueError("metadata too large")
    return _validate(json.loads(RECORD.read_text(encoding="utf-8")), history)


def _revision(config, record, history):
    mapped = {**record, "project": runtime.PROJECT,
              "state": "migration_pending" if record["state"] in {"migration_pending", "migration_issued"} else "ready"}
    return runtime.current_revision(runtime.verify_database(config, mapped), history[1], allow_empty=True)


def _main_database():
    # Do not accept a forged K3s record that names the separately retained local
    # runtime, even when its owner and sentinel happen to be valid.
    if _unsafe(runtime.LOCAL) or _unsafe(runtime.RECORD):
        raise ValueError("unsafe main runtime metadata")
    if runtime.RECORD.exists():
        return runtime.read_record()["database"]
    return None


def _distinct(record):
    if record["database"] == _main_database():
        raise ValueError("K3s database aliases retained runtime")


def _get(config, history):
    record = _read(history)
    _distinct(record)
    current = _revision(config, record, history)
    if record["state"] in {"migration_pending", "migration_issued"}:
        raise ValueError("migration outcome pending; reconcile first")
    if current != record["revision"]:
        raise ValueError("database revision differs from metadata")
    return record


def get_record():
    with _lock():
        return _get(db.config(CONNECTION), _history())


def initialize():
    """Create once, or validate the existing record; never run migrations."""
    with _lock():
        history = _history()
        config = db.config(CONNECTION)
        if RECORD.exists():
            return _get(config, history)
        _main_database()
        # Probe before the durable provisioning marker, then never repeat a
        # partially observed CREATE automatically.
        with db.connect(config):
            pass
        _write({"synthetic_only": True, "do_not_drop": True, "project": PROJECT,
                "state": "provisioning", "history_sha256": history[2]}, exclusive=True)
        name, nonce, urls = db.create(config)
        record = {"synthetic_only": True, "do_not_drop": True, "project": PROJECT,
                  "state": "created", "database": name, "nonce": nonce, "urls": urls,
                  "revision": None, "history_sha256": history[2]}
        _validate(record, history)
        _distinct(record)
        if _revision(config, record, history) is not None:
            raise ValueError("new database not empty")
        _write(record)
        return record


def prepare_migration():
    """Persist target before a separately authorized external migration Job."""
    with _lock():
        history, config = _history(), db.config(CONNECTION)
        record = _get(config, history)
        if record["revision"] == TARGET:
            return record
        record.update(state="migration_pending", target_revision=TARGET)
        _write(record)
        return record


def reconcile_migration():
    """Mark ready only when the exact pending target is observed, never rerun."""
    with _lock():
        history, config = _history(), db.config(CONNECTION)
        record = _read(history)
        _distinct(record)
        if record["state"] not in {"migration_pending", "migration_issued"}:
            raise ValueError("no pending migration")
        if _revision(config, record, history) != TARGET:
            raise ValueError("migration outcome unresolved; preserve state")
        record.update(state="ready", revision=TARGET)
        del record["target_revision"]
        _write(record)
        return record


def _attested_ip():
    env = dict(os.environ)
    context = docker.local_context(env)
    prefix = ["docker", "--context", context]
    info = docker.parse_json(docker.command(prefix + ["inspect", "--type", "container", docker.TARGET], env))
    container_id, _, _ = docker.verify_container(info)
    docker.verify_volume(docker.parse_json(docker.command(prefix + ["volume", "inspect", docker.VOLUME], env)))
    network = docker.parse_json(docker.command(prefix + ["network", "inspect", NETWORK], env))
    try:
        obj = network[0]
        attachment = info[0]["NetworkSettings"]["Networks"][NETWORK]
        endpoint = obj["Containers"][container_id]
        ip = ipaddress.ip_address(attachment["IPAddress"])
        attached = ipaddress.ip_interface(endpoint["IPv4Address"])
        private_networks = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if (len(network) != 1 or obj["Name"] != NETWORK or obj["Id"] != attachment["NetworkID"]
                or obj["Driver"] != "bridge" or obj["Scope"] != "local"
                or obj["Labels"].get("com.docker.compose.project") != docker.PROJECT
                or endpoint["Name"] != docker.TARGET or ip.version != 4 or not any(ip in block for block in private_networks)
                or ip != attached.ip):
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("synthetic network identity mismatch") from None
    return str(ip)


def _mapped_urls(urls, ip):
    result = {}
    for role, url in urls.items():
        parts = urlsplit(url)
        # URLs were validated above; preserve quoted credentials without logging.
        userinfo = parts.netloc.rsplit("@", 1)[0]
        result[role] = urlunsplit((parts.scheme, f"{userinfo}@{ip}:5432", parts.path, "", ""))
    return result


def migration_role_url(record):
    """Return only the migration credential for a not-yet-observed Job."""
    with _lock():
        history, config = _history(), db.config(CONNECTION)
        saved = _read(history)
        _distinct(saved)
        if saved != record or saved["state"] != "migration_pending":
            raise ValueError("no matching pending migration")
        if _revision(config, saved, history) != saved["revision"]:
            raise ValueError("migration outcome changed; reconcile first")
        mapped = _mapped_urls({"migrate": saved["urls"]["migrate"]}, _attested_ip())["migrate"]
        # Persist before issuing the credential: lost responses cannot trigger
        # an automatic second Job, even if the revision still looks old.
        saved["state"] = "migration_issued"
        _write(saved)
        return mapped


def container_role_urls(record):
    """Return private URLs only after checking database and Docker identities."""
    with _lock():
        saved = _get(db.config(CONNECTION), _history())
        if record != saved or saved["state"] != "ready" or saved["revision"] != TARGET:
            raise ValueError("K3s database not ready")
        return _mapped_urls(saved["urls"], _attested_ip())
