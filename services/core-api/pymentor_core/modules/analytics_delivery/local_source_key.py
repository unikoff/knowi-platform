"""Validate and read the one private local Analytics source key."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from uuid import UUID


_SERVICE = Path(__file__).resolve().parents[4] / "analytics"


def _unlinked(path):
    if path.is_symlink() or os.path.isjunction(path):
        raise ValueError("Unsafe local source path")


def _windows_private(path):
    root = os.environ.get("SystemRoot")
    if not root or not Path(root).is_absolute():
        raise ValueError("Windows system path unavailable")
    executable = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = ("$ErrorActionPreference='Stop'; "
        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
        "$u=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        "$a=Get-Acl -LiteralPath $env:CORE_ANALYTICS_SOURCE_ACL_PATH; "
        "$o=$a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value; "
        "$r=$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]); "
        "@{user=$u;owner=$o;rules=@($r | ForEach-Object {"
        "@{sid=$_.IdentityReference.Value;allow=($_.AccessControlType -eq 'Allow');"
        "rights=[int]$_.FileSystemRights}})} | ConvertTo-Json -Depth 5 -Compress")
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "CORE_ANALYTICS_SOURCE_ACL_PATH": str(path)}, capture_output=True,
        timeout=10, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if len(result.stdout) > 65536:
        raise ValueError("Invalid local ACL")
    acl = json.loads(result.stdout.decode("utf-8-sig"))
    user, owner, rules = acl.get("user"), acl.get("owner"), acl.get("rules")
    if (not isinstance(user, str) or not (user == "S-1-5-18" or re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", user))
            or owner not in {user, "S-1-5-18"} or not isinstance(rules, list) or not rules):
        raise ValueError("Invalid local ACL")
    if any(rule.get("allow") is True and rule.get("sid") not in {user, "S-1-5-18"} for rule in rules):
        raise ValueError("Invalid local ACL")
    if not any(rule.get("allow") is True and rule.get("sid") == user and type(rule.get("rights")) is int
               and (rule["rights"] & 0x1F01FF) == 0x1F01FF for rule in rules):
        raise ValueError("Invalid local ACL")


def _private(path, *, directory):
    _unlinked(path)
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Invalid local directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256:
        raise ValueError("Invalid local file")
    if os.name == "nt":
        _windows_private(path)
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError("Invalid local permissions")


def read_source_key(path_value, dataset_id):
    if not isinstance(dataset_id, str) or str(UUID(dataset_id)) != dataset_id or UUID(dataset_id).int == 0:
        raise ValueError("Invalid local dataset")
    service = _SERVICE.resolve(strict=True)
    _unlinked(_SERVICE)
    local = service / ".local"
    root = local / "local-live"
    instance = root / dataset_id
    path = instance / "source.key"
    if Path(path_value).absolute() != path:
        raise ValueError("Invalid local source path")
    for directory in (local, root, instance):
        _unlinked(directory)
        if not directory.is_dir() or directory.resolve(strict=True) != directory:
            raise ValueError("Invalid local directory")
    _private(root, directory=True)
    _private(instance, directory=True)
    _private(path, directory=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        actual, expected = os.fstat(stream.fileno()), path.stat()
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
            raise ValueError("Unsafe local file")
        raw = stream.read(257)
    _private(root, directory=True)
    _private(instance, directory=True)
    _private(path, directory=False)
    final = path.stat()
    if (actual.st_dev, actual.st_ino) != (final.st_dev, final.st_ino):
        raise ValueError("Unsafe local file")
    if (len(raw) != 43 or not re.fullmatch(rb"[A-Za-z0-9_-]{43}", raw)
            or base64.urlsafe_b64encode(base64.urlsafe_b64decode(raw + b"=")).rstrip(b"=") != raw):
        raise ValueError("Invalid local source key")
    return raw
