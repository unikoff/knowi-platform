"""Explicit personal-local authority; never opens a network beyond loopback."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from pymentor_analytics.adapters.privacy_authority.client import _unique
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.adapters.privacy_authority.server import BoundedFixtureProtocol, MAX_CONNECTIONS, create_authority_app
from pymentor_analytics.contracts.v2 import Identifier

SERVICE = Path(__file__).resolve().parents[3]
LOCAL_ROOT = SERVICE / ".local" / "local-live"
SERVER_FILE = "authority-server.json"
JOURNAL_FILE = "authority.sqlite"
OWNER_FILE = "owner.json"
MAX_CONFIG = 128 * 1024


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("invalid local identity")
    parsed = UUID(value)
    if parsed.int == 0 or str(parsed) != value:
        raise ValueError("invalid local identity")
    return value


def _unlinked(path: Path):
    if path.is_symlink() or path.is_junction():
        raise ValueError("unsafe local path")


def _windows_private(path: Path):
    """Read DACL only: no broad principal may access a local authority file."""
    system_root = os.environ.get("SystemRoot")
    if not system_root or not Path(system_root).is_absolute():
        raise ValueError("Windows system path unavailable")
    system = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = ("$ErrorActionPreference='Stop'; "
               "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
               "$u=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
               "$a=Get-Acl -LiteralPath $env:ANALYTICS_LOCAL_ACL_PATH; "
               "$o=$a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value; "
               "$r=$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]); "
               "@{user=$u;owner=$o;rules=@($r | ForEach-Object {"
               "@{sid=$_.IdentityReference.Value;allow=($_.AccessControlType -eq 'Allow');"
               "rights=[int]$_.FileSystemRights}})} | ConvertTo-Json -Depth 5 -Compress")
    result = subprocess.run([str(system), "-NoProfile", "-NonInteractive", "-Command", command],
                            env={**os.environ, "ANALYTICS_LOCAL_ACL_PATH": str(path)},
                            capture_output=True, timeout=10, check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    acl = json.loads(result.stdout.decode("utf-8-sig"))
    user, owner, rules = acl.get("user"), acl.get("owner"), acl.get("rules")
    if (not isinstance(user, str) or not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", user)
            or owner not in {user, "S-1-5-18"} or not isinstance(rules, list) or not rules):
        raise ValueError("private local ACL unavailable")
    if any(rule.get("allow") is True and rule.get("sid") not in {user, "S-1-5-18"} for rule in rules):
        raise ValueError("private local ACL unavailable")
    if not any(rule.get("allow") is True and rule.get("sid") == user
               and (rule.get("rights", 0) & 0x1F01FF) == 0x1F01FF for rule in rules):
        raise ValueError("private local ACL unavailable")


def _private(path: Path, *, directory=False):
    _unlinked(path)
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid local directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("invalid local file")
    if os.name == "nt":
        _windows_private(path)
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("private local permissions required")


def _instance_dir(instance_id: str) -> Path:
    instance_id = _uuid(instance_id)
    service = SERVICE.resolve(strict=True)
    root = LOCAL_ROOT.absolute()
    if root != service / ".local" / "local-live":
        raise ValueError("invalid local root")
    for path in (service / ".local", root, root / instance_id):
        _unlinked(path)
        if not path.is_dir() or path.resolve(strict=True) != path:
            raise ValueError("invalid local directory")
    instance = root / instance_id
    _private(root, directory=True)
    _private(instance, directory=True)
    return instance


def _file(instance: Path, name: str, *, required=True) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise ValueError("invalid local file name")
    path = instance / name
    _unlinked(path)
    if path.parent != instance or path.resolve(strict=False).parent != instance:
        raise ValueError("unsafe local file")
    if required or path.exists():
        _private(path)
    return path


def _read(instance: Path, name: str, limit: int) -> bytes:
    path = _file(instance, name)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        actual = os.fstat(stream.fileno())
        expected = path.stat()
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
            raise ValueError("unsafe local file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("local file exceeds bound")
    return data


def _write_new(path: Path, data: bytes) -> None:
    """Persist a new private file; callers validate its instance and basename."""
    if not path.is_absolute() or not isinstance(data, bytes) or len(data) > 256 * 1024:
        raise ValueError("invalid local private write")
    _unlinked(path)
    _private(path.parent, directory=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe local private write")
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _private(path)


def _owner(instance: Path, instance_id: str):
    value = json.loads(_read(instance, OWNER_FILE, 8192), object_pairs_hook=_unique)
    keys = {"version", "mode", "do_not_drop", "instance_id", "core_instance_id", "dataset_run_id", "authority_id"}
    if (not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int or value["version"] != 1
            or value["mode"] != "local" or value["do_not_drop"] is not True or value["instance_id"] != instance_id):
        raise ValueError("invalid local owner")
    _uuid(value["instance_id"])
    _uuid(value["core_instance_id"])
    _uuid(value["dataset_run_id"])
    TypeAdapter(Identifier).validate_python(value["authority_id"], strict=True)
    if value["authority_id"] != "local-authority:" + instance_id or value["core_instance_id"] == instance_id:
        raise ValueError("invalid local owner identity")
    return value


def initialize(instance_id: str):
    """Initialize once; failures preserve any incomplete journal for inspection."""
    instance = _instance_dir(instance_id)
    owner = _owner(instance, instance_id)
    path = _file(instance, JOURNAL_FILE, required=False)
    if path.exists():
        raise ValueError("local journal already exists")
    for suffix in ("-journal", "-wal", "-shm"):
        if _file(instance, JOURNAL_FILE + suffix, required=False).exists():
            raise ValueError("local journal state unresolved")
    previous = os.umask(0o077) if os.name != "nt" else None
    try:
        Journal.initialize(path, owner["authority_id"])
    finally:
        if previous is not None:
            os.umask(previous)
    _file(instance, JOURNAL_FILE)


def load_application(instance_id: str):
    """Validate owner, ACL, regular files and journal before creating the app."""
    instance = _instance_dir(instance_id)
    owner = _owner(instance, instance_id)
    config = json.loads(_read(instance, SERVER_FILE, MAX_CONFIG), object_pairs_hook=_unique)
    required = {"mode", "fixture_mode", "bind_host", "port", "issuer", "kid", "signing_key_file",
                "producer_issuer", "producer_public_keys", "allowed_producers", "producer_command_public_keys", "reader_secret_file"}
    if (not isinstance(config, dict) or set(config) != required or config["mode"] != "local"
            or config["fixture_mode"] is not False or config["bind_host"] != "127.0.0.1"
            or type(config["port"]) is not int or config["port"] != 18046
            or config["issuer"] != owner["authority_id"] or config["producer_issuer"] != "local-producer:" + instance_id
            or config["signing_key_file"] != "authority.private.pem" or config["reader_secret_file"] != "authority.reader"
            or not isinstance(config["producer_public_keys"], dict) or not isinstance(config["producer_command_public_keys"], dict)
            or config["allowed_producers"] != ["core-local"]):
        raise ValueError("invalid local authority configuration")
    key_data = _read(instance, config["signing_key_file"], 8192)
    secret = _read(instance, config["reader_secret_file"], 256).decode("ascii").strip()
    key = serialization.load_pem_private_key(key_data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("invalid local signing key")
    journal = _file(instance, JOURNAL_FILE)
    for suffix in ("-journal", "-wal", "-shm"):
        _file(instance, JOURNAL_FILE + suffix, required=False)
    app = create_authority_app(Journal(journal, owner["authority_id"]), issuer=config["issuer"], kid=config["kid"],
        signing_key=key, producer_issuer=config["producer_issuer"],
        producer_public_keys={kid: pem.encode("ascii") for kid, pem in config["producer_public_keys"].items()},
        allowed_producers=set(config["allowed_producers"]),
        producer_command_public_keys={producer: pem.encode("ascii") for producer, pem in config["producer_command_public_keys"].items()},
        reader_secret=secret)
    return app


def main():
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("invalid local authority arguments")
    parser = SafeParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
    for action in ("init", "serve"):
        command = sub.add_parser(action)
        command.add_argument("--instance-id", required=True, type=_uuid)
    try:
        args = parser.parse_args()
        if args.action == "init":
            initialize(args.instance_id)
            print("local authority journal initialized")
            return
        app = load_application(args.instance_id)
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=18046, http=BoundedFixtureProtocol, ws="none",
                    backlog=16, limit_concurrency=MAX_CONNECTIONS, timeout_keep_alive=2,
                    h11_max_incomplete_event_size=8192, proxy_headers=False, access_log=False,
                    log_level="critical", log_config=None)
    except Exception:
        print("local authority unavailable; preserve state for reconciliation", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
