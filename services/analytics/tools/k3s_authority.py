"""Prepare an independent synthetic authority; mutating Docker needs caller GO.

No Docker operation occurs at import or during prepare. CLI deliberately exposes
only prepare/status; ``start(..., authorized=True)`` is for an authorized caller.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import sys
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

SERVICE = Path(__file__).resolve().parents[1]
TOOLS = SERVICE / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(SERVICE))
import check_k3s
import resource_snapshot
from pymentor_analytics.adapters.auth.config import load_manifest
from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig

PROJECT = "knowi-analytics-01a1df75"
NETWORK = PROJECT + "-net"
FIXTURE = "k3s-privacy-authority"
PORT = 57420
CLIENT_URL = "http://privacy-authority.knowi-analytics.svc:57420"
LOCAL = SERVICE / ".local"
ROOT = LOCAL / "k3s-authority"
TRUST = LOCAL / "trust-manifest.json"
DATA_DIR = "/app/.local/data"
CONFIG_DIR = "/app/.local/config"
JOURNAL = DATA_DIR + "/authority.sqlite"
EXPECTED_FILES = ("config/authority.key", "config/reader.secret", "config/server.json",
                  "command-private.pem", "command-public.pem", "client-secret.json")


class Refused(Exception):
    """Only sanitized reason codes may cross the helper boundary."""


@dataclass(frozen=True)
class Prepared:
    authority_id: str
    nonce: str
    volume_name: str
    client_config_path: Path
    trust_manifest_path: Path
    command_public_path: Path
    command_private_path: Path


@dataclass(frozen=True)
class Status:
    ip: str
    container_id: str
    authority_id: str


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _safe(path: Path, *, must_exist=True, directory=False) -> Path:
    """Reject escape, links, junctions and hard-linked regular files."""
    service = SERVICE.resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(service)
    except ValueError:
        raise Refused("unsafe_path") from None
    current = service
    for part in relative.parts:
        if part in ("", ".", ".."):
            raise Refused("unsafe_path")
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise Refused("unsafe_path")
        if current.exists() and current.is_file() and current.stat().st_nlink != 1:
            raise Refused("unsafe_path")
    if current.resolve(strict=False) != candidate:
        raise Refused("unsafe_path")
    if must_exist:
        try:
            info = current.lstat()
            if directory and not stat.S_ISDIR(info.st_mode):
                raise Refused("unsafe_path")
            if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise Refused("unsafe_path")
        except OSError:
            raise Refused("unsafe_path") from None
    return candidate


def _read(path: Path, limit=128 * 1024) -> bytes:
    path = _safe(path)
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise Refused("file_too_large")
        return data
    except OSError:
        raise Refused("file_unavailable") from None


def _write_new(path: Path, data: bytes) -> None:
    _safe(path.parent, directory=True)
    path = _safe(path, must_exist=False)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(path)
    except OSError:
        raise Refused("exclusive_write_failed") from None


def _private_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _trust():
    raw = _read(TRUST)
    try:
        grant, producer, fingerprint = load_manifest(SecretStr(raw.decode("utf-8")))
        data = _json(raw)
        if (set(data["grant"]["keys"]) != {"g1"} or set(data["producer"]["keys"]) != {"p1"}
                or data["producer"]["allowlist"] != ["synthetic-producer"]):
            raise ValueError
        return raw, data, fingerprint
    except Exception:
        raise Refused("invalid_public_trust") from None


def _result(meta):
    return Prepared(meta["authority_id"], meta["nonce"], meta["volume_name"],
                    ROOT / "client-secret.json", TRUST, ROOT / "command-public.pem", ROOT / "command-private.pem")


def _verify_prepared():
    _safe(ROOT, directory=True)
    _safe(ROOT / "config", directory=True)
    try:
        meta = _json(_read(ROOT / "owner.json"))
        if not isinstance(meta, dict) or set(meta) != {"version", "project", "fixture", "nonce", "authority_id", "volume_name", "trust_sha256", "files"}:
            raise ValueError
        nonce = meta["nonce"]
        if (type(meta["version"]) is not int or meta["version"] != 1 or meta["project"] != PROJECT or meta["fixture"] != FIXTURE
                or not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
                or meta["authority_id"] != "synthetic-k3s-authority-" + nonce
                or meta["volume_name"] != PROJECT + "-authority-" + nonce
                or not isinstance(meta["files"], dict) or set(meta["files"]) != set(EXPECTED_FILES)):
            raise ValueError
        if {p.name for p in ROOT.iterdir()} != {"owner.json", "config", "command-private.pem", "command-public.pem", "client-secret.json"}:
            raise ValueError
        if {p.name for p in (ROOT / "config").iterdir()} != {"authority.key", "reader.secret", "server.json"}:
            raise ValueError
        for rel in EXPECTED_FILES:
            if hashlib.sha256(_read(ROOT / rel)).hexdigest() != meta["files"][rel]:
                raise ValueError
        trust_raw, trust, _ = _trust()
        if hashlib.sha256(trust_raw).hexdigest() != meta["trust_sha256"]:
            raise ValueError
        client = AuthorityConfig.from_json(_read(ROOT / "client-secret.json"))
        private = serialization.load_pem_private_key(_read(ROOT / "config/authority.key", 8192), password=None)
        command = serialization.load_pem_private_key(_read(ROOT / "command-private.pem", 8192), password=None)
        if not isinstance(private, Ed25519PrivateKey) or not isinstance(command, Ed25519PrivateKey):
            raise ValueError
        response_pub, command_pub = _public_bytes(private), _public_bytes(command)
        grant_pub = trust["grant"]["keys"]["g1"].encode("ascii")
        producer_pub = trust["producer"]["keys"]["p1"].encode("ascii")
        raw_keys = [serialization.load_pem_public_key(value).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                    for value in (response_pub, command_pub, grant_pub, producer_pub)]
        if len(set(raw_keys)) != 4 or command_pub != _read(ROOT / "command-public.pem", 8192):
            raise ValueError
        reader = _read(ROOT / "config/reader.secret", 256).decode("ascii")
        expected_server = _server_config(meta["authority_id"], trust, command_pub.decode("ascii"))
        if (_json(_read(ROOT / "config/server.json")) != expected_server or client.url != CLIENT_URL
                or client.authority_id != meta["authority_id"] or client.issuer != "synthetic-k3s-authority"
                or client.public_keys != {"a1": response_pub.decode("ascii")}
                or client.reader_secret.get_secret_value() != reader):
            raise ValueError
        return _result(meta)
    except Refused:
        raise
    except Exception:
        raise Refused("prepared_state_unverified") from None


def _server_config(authority_id, trust, command_public):
    return {"fixture_mode": True, "bind_host": "0.0.0.0", "port": PORT, "issuer": "synthetic-k3s-authority",
            "kid": "a1", "signing_key_file": CONFIG_DIR + "/authority.key",
            "producer_issuer": trust["producer"]["issuer"], "producer_public_keys": trust["producer"]["keys"],
            "allowed_producers": ["synthetic-producer"],
            "producer_command_public_keys": {"synthetic-producer": command_public},
            "reader_secret_file": CONFIG_DIR + "/reader.secret"}


def prepare() -> Prepared:
    """Create exclusively or verify one known fixture; no Docker calls."""
    _safe(LOCAL, directory=True)
    trust_raw, trust, _ = _trust()
    _safe(ROOT, must_exist=False)
    if ROOT.exists():
        return _verify_prepared()
    nonce = uuid4().hex
    authority_id = "synthetic-k3s-authority-" + nonce
    response, command = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    response_pub, command_pub = _public_bytes(response), _public_bytes(command)
    reader = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    client = {"url": CLIENT_URL, "authority_id": authority_id, "issuer": "synthetic-k3s-authority",
              "public_keys": {"a1": response_pub.decode("ascii")}, "reader_secret": reader, "fixture_mode": True}
    AuthorityConfig.from_json(json.dumps(client))
    values = {"config/authority.key": _private_bytes(response), "config/reader.secret": reader.encode("ascii"),
              "config/server.json": json.dumps(_server_config(authority_id, trust, command_pub.decode("ascii")), sort_keys=True).encode(),
              "command-private.pem": _private_bytes(command), "command-public.pem": command_pub,
              "client-secret.json": json.dumps(client, sort_keys=True).encode()}
    try:
        os.mkdir(_safe(ROOT, must_exist=False), 0o700)
        os.mkdir(_safe(ROOT / "config", must_exist=False), 0o700)
    except OSError:
        raise Refused("exclusive_directory_failed") from None
    for name, value in values.items():
        _write_new(ROOT / name, value)
    meta = {"version": 1, "project": PROJECT, "fixture": FIXTURE, "nonce": nonce,
            "authority_id": authority_id, "volume_name": PROJECT + "-authority-" + nonce,
            "trust_sha256": hashlib.sha256(trust_raw).hexdigest(),
            "files": {name: hashlib.sha256(value).hexdigest() for name, value in values.items()}}
    _write_new(ROOT / "owner.json", json.dumps(meta, sort_keys=True).encode())
    return _verify_prepared()


def _run(args, *, timeout=20):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise Refused("docker_command_failed")
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        # An interrupted mutation has an unknown outcome: preserve and reconcile.
        raise Refused("docker_outcome_unknown") from None


def _inspect(raw):
    try:
        value = _json(raw)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise ValueError
        return value[0]
    except Exception:
        raise Refused("docker_metadata_invalid") from None


def _image_id(value):
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise Refused("image_id_invalid")
    return value


def _labels(prepared):
    return {"knowi.analytics.synthetic": "true", "knowi.analytics.fixture": FIXTURE,
            "knowi.analytics.project": PROJECT, "knowi.analytics.nonce": prepared.nonce}


def _name(prepared):
    return PROJECT + "-privacy-authority-" + prepared.nonce


def _capacity():
    try:
        if not check_k3s.admission(check_k3s.available_memory()):
            raise Refused("insufficient_host_memory")
    except Refused:
        raise
    except Exception:
        raise Refused("host_memory_unverified") from None


def _network_subnet(network):
    try:
        values = [ipaddress.ip_network(entry["Subnet"], strict=True) for entry in network["IPAM"]["Config"] if "Subnet" in entry]
        ipv4 = [value for value in values if value.version == 4]
        private_ranges = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if len(ipv4) != 1 or not any(ipv4[0].subnet_of(value) for value in private_ranges) or ipv4[0].prefixlen > 30:
            raise ValueError
        return ipv4[0]
    except (KeyError, TypeError, ValueError):
        raise Refused("network_identity_unverified") from None


def _environment(image_id):
    image_id = _image_id(image_id)
    try:
        context = resource_snapshot.local_context(dict(os.environ))
    except Exception:
        raise Refused("docker_context_unverified") from None
    docker = ["docker", "--context", context]
    image = _inspect(_run(docker + ["image", "inspect", image_id]))
    try:
        tags = image["RepoTags"] or []
        if (image["Id"] != image_id or image["Os"] != "linux" or image["Architecture"] != "amd64"
                or image["Config"]["User"] != "10001" or image["Config"]["WorkingDir"] != "/app"
                or not any(isinstance(tag, str) and tag.startswith("knowi-analytics:") for tag in tags)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise Refused("image_unverified") from None
    pg_list = _json(_run(docker + ["inspect", "--type", "container", resource_snapshot.TARGET]))
    try:
        pg_id, _, _ = resource_snapshot.verify_container(pg_list)
        resource_snapshot.verify_volume(_json(_run(docker + ["volume", "inspect", resource_snapshot.VOLUME])))
    except Exception:
        raise Refused("postgres_identity_unverified") from None
    network = _inspect(_run(docker + ["network", "inspect", NETWORK]))
    try:
        subnet = _network_subnet(network)
        labels = network["Labels"]
        pg = pg_list[0]["NetworkSettings"]["Networks"][NETWORK]
        linked = network["Containers"][pg_id]
        if (network["Name"] != NETWORK or network["Driver"] != "bridge"
                or labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.network") != "analytics"
                or not re.fullmatch(r"[0-9a-f]{64}", network["Id"])
                or pg["NetworkID"] != network["Id"] or linked["Name"] != resource_snapshot.TARGET
                or ipaddress.ip_interface(linked["IPv4Address"]).ip != ipaddress.ip_address(pg["IPAddress"])
                or ipaddress.ip_address(pg["IPAddress"]) not in subnet):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused("network_identity_unverified") from None
    return docker, network


def _verify_volume(obj, prepared):
    try:
        if (obj["Name"] != prepared.volume_name or obj["Driver"] != "local"
                or any(obj["Labels"].get(k) != v for k, v in _labels(prepared).items())):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise Refused("authority_volume_unverified") from None


def _bind_source_matches(source: str, expected: str, *, windows: bool) -> bool:
    """Docker Desktop reports Windows binds as /run/desktop/mnt/host/<drive>/... ."""
    if not isinstance(source, str) or not isinstance(expected, str):
        return False
    if not windows:
        return os.path.normcase(os.path.abspath(source)) == os.path.normcase(expected)
    path = PureWindowsPath(expected)
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":":
        return False
    if any(part in (".", "..") for part in path.parts[1:]):
        return False
    mapped = "/run/desktop/mnt/host/" + path.drive[0].lower() + "/" + "/".join(path.parts[1:])
    return source.casefold() == expected.casefold() or source.casefold() == mapped.casefold()


def _verify_container(obj, prepared, image_id, network):
    try:
        labels, host = obj["Config"]["Labels"], obj["HostConfig"]
        net = obj["NetworkSettings"]["Networks"]
        mounts = obj["Mounts"]
        data = [m for m in mounts if m.get("Destination") == DATA_DIR]
        config = [m for m in mounts if m.get("Destination") == CONFIG_DIR]
        binding = host.get("PortBindings") or {}
        exposed = obj["NetworkSettings"].get("Ports") or {}
        expected_config = os.path.normcase(str(_safe(ROOT / "config", directory=True)))
        linked = network["Containers"][obj["Id"]]
        ip = ipaddress.ip_address(net[NETWORK]["IPAddress"])
        subnet = _network_subnet(network)
        config_matches = len(config) == 1 and _bind_source_matches(config[0]["Source"], expected_config, windows=os.name == "nt")
        expected_command = ["-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve", "--private-fixture-network",
                            "--journal", JOURNAL, "--authority-id", prepared.authority_id, "--config", CONFIG_DIR + "/server.json"]
        if (obj["Name"] != "/" + _name(prepared) or obj["Image"] != image_id
                or obj["State"]["Running"] is not True or not re.fullmatch(r"[0-9a-f]{64}", obj["Id"])
                or any(labels.get(k) != v for k, v in _labels(prepared).items())
                or labels.get("knowi.analytics.role") != "authority"
                or obj["Config"]["User"] != "10001:10001" or obj["Config"]["Entrypoint"] != ["python"]
                or obj["Config"]["Cmd"] != expected_command or host["ReadonlyRootfs"] is not True
                or host["Memory"] != 128 * 1024 * 1024 or host["NanoCpus"] != 250_000_000
                or host.get("Privileged") is not False or host.get("PidsLimit") != 64
                or "ALL" not in (host.get("CapDrop") or []) or host.get("CapAdd")
                or not any(x in ("no-new-privileges", "no-new-privileges:true") for x in (host.get("SecurityOpt") or []))
                or binding or any(value for value in exposed.values())
                or len(mounts) != 2 or len(data) != 1 or data[0].get("Type") != "volume"
                or data[0].get("Name") != prepared.volume_name or data[0].get("RW") is not True
                or len(config) != 1 or config[0].get("Type") != "bind" or config[0].get("RW") is not False
                or not config_matches or set(net) != {NETWORK} or net[NETWORK]["NetworkID"] != network["Id"]
                or linked["Name"] != _name(prepared) or ipaddress.ip_interface(linked["IPv4Address"]).ip != ip
                or ip.version != 4 or ip not in subnet or ip in (subnet.network_address, subnet.broadcast_address)
                or not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved):
            raise ValueError
        return Status(str(ip), obj["Id"], prepared.authority_id)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise Refused("authority_container_unverified") from None


def status(image_id: str) -> Status:
    """Read-only Docker metadata attestation; does not claim source readiness."""
    prepared = _verify_prepared()
    docker, network = _environment(image_id)
    volume = _inspect(_run(docker + ["volume", "inspect", prepared.volume_name]))
    _verify_volume(volume, prepared)
    container = _inspect(_run(docker + ["inspect", "--type", "container", _name(prepared)]))
    return _verify_container(container, prepared, image_id, network)


def _limits(prepared, *, role):
    args = ["--read-only", "--memory", "128m", "--cpus", "0.25", "--pids-limit", "64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
    for key, value in _labels(prepared).items():
        args += ["--label", key + "=" + value]
    return args + ["--label", "knowi.analytics.role=" + role]


def _one_shot(docker, prepared, image_id, role, *, mount, code, root=False):
    _capacity()
    args = docker + ["run", "--rm", "--pull", "never", "--network", "none",
                     "--name", _name(prepared) + "-" + role, "--user", "0:0" if root else "10001:10001"]
    args += _limits(prepared, role=role)
    if root:
        args += ["--cap-add", "CHOWN"]
    args += ["--mount", mount, "--entrypoint", "python", image_id, "-B", "-c", code]
    _run(args, timeout=30)


def start(image_id: str, *, authorized: bool = False) -> Status:
    """After explicit caller GO only. Unknown/partial state is never reset."""
    if authorized is not True:
        raise Refused("explicit_go_required")
    prepared = _verify_prepared()
    _capacity()
    docker, _ = _environment(image_id)
    # Serialize our own starts. The marker survives any error or unknown
    # outcome; only a fully attested success removes this exact owned file.
    marker = LOCAL / ("k3s-authority-start-" + prepared.nonce + ".pending")
    marker_data = (prepared.authority_id + "\n").encode("ascii")
    _write_new(marker, marker_data)
    result = _start_locked(prepared, image_id, docker)
    if _read(marker, 256) != marker_data:
        raise Refused("operation_marker_unverified")
    _safe(marker).unlink()
    return result


def reconcile_started(image_id: str, *, authorized: bool = False) -> Status:
    """Resolve only a verified running authority; never retry creation or delete data."""
    if authorized is not True:
        raise Refused("explicit_go_required")
    prepared = _verify_prepared()
    marker = LOCAL / ("k3s-authority-start-" + prepared.nonce + ".pending")
    marker_data = (prepared.authority_id + "\n").encode("ascii")
    if _read(marker, 256) != marker_data:
        raise Refused("operation_marker_unverified")
    result = status(image_id)
    if result.authority_id != prepared.authority_id or _read(marker, 256) != marker_data:
        raise Refused("operation_marker_unverified")
    _safe(marker).unlink()
    return result


def _start_locked(prepared: Prepared, image_id: str, docker: list[str]) -> Status:
    names = set(_run(docker + ["ps", "-a", "--format", "{{.Names}}"] ).splitlines())
    if _name(prepared) in names:
        return status(image_id)  # rejects stopped, foreign or mismatching state
    if any(_name(prepared) + "-" + role in names for role in ("config-probe", "volume-owner", "journal-init", "journal-verify")):
        raise Refused("partial_helper_state")
    volumes = set(_run(docker + ["volume", "ls", "--format", "{{.Name}}"] ).splitlines())
    exists = prepared.volume_name in volumes
    if exists:
        _verify_volume(_inspect(_run(docker + ["volume", "inspect", prepared.volume_name])), prepared)
    config_path = str(_safe(ROOT / "config", directory=True))
    if "," in config_path or "\n" in config_path or "\r" in config_path:
        raise Refused("unsafe_mount_path")
    config_mount = "type=bind,source=" + config_path + ",target=" + CONFIG_DIR + ",readonly"
    volume_mount = "type=volume,source=" + prepared.volume_name + ",target=" + DATA_DIR + ",volume-nocopy"
    # Verify UID 10001 can read its exact private read-only bind before creating
    # durable storage; never widen host permissions automatically.
    probe = "from pathlib import Path; p=Path('/app/.local/config'); [(p/n).read_bytes() for n in ('authority.key','reader.secret','server.json')]"
    _one_shot(docker, prepared, image_id, "config-probe", mount=config_mount, code=probe)
    if not exists:
        _capacity()
        labels = [item for key, value in _labels(prepared).items() for item in ("--label", key + "=" + value)]
        created = _run(docker + ["volume", "create", "--driver", "local", *labels, prepared.volume_name])
        if created != prepared.volume_name:
            raise Refused("volume_creation_unreconciled")
        _verify_volume(_inspect(_run(docker + ["volume", "inspect", prepared.volume_name])), prepared)
        chown = "import os\nfrom pathlib import Path\np=Path('/app/.local/data')\nif any(p.iterdir()): raise SystemExit(1)\nos.chown(p,10001,10001)"
        _one_shot(docker, prepared, image_id, "volume-owner", mount=volume_mount, code=chown, root=True)
        initialize = ("from pathlib import Path; from pymentor_analytics.adapters.privacy_authority.journal import Journal; "
                      "Journal.initialize(Path('" + JOURNAL + "'),'" + prepared.authority_id + "')")
        _one_shot(docker, prepared, image_id, "journal-init", mount=volume_mount, code=initialize)
    # An existing or newly initialized volume must prove the journal identity.
    verify = ("from pathlib import Path; from pymentor_analytics.adapters.privacy_authority.journal import Journal; "
              "Journal(Path('" + JOURNAL + "'),'" + prepared.authority_id + "')")
    _one_shot(docker, prepared, image_id, "journal-verify", mount=volume_mount, code=verify)
    _capacity()
    args = docker + ["run", "-d", "--pull", "never", "--name", _name(prepared),
                     "--network", NETWORK, "--user", "10001:10001"]
    args += _limits(prepared, role="authority")
    args += ["--mount", volume_mount, "--mount", config_mount, "--entrypoint", "python", image_id,
             "-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve", "--private-fixture-network",
             "--journal", JOURNAL, "--authority-id", prepared.authority_id, "--config", CONFIG_DIR + "/server.json"]
    created = _run(args, timeout=30)
    if re.fullmatch(r"[0-9a-f]{64}", created) is None:
        raise Refused("container_creation_unreconciled")
    result = status(image_id)
    if result.container_id != created:
        raise Refused("container_creation_unreconciled")
    return result


def main():
    """Phase one deliberately exposes no Docker-mutating CLI action."""
    if sys.argv[1:] != ["prepare"]:
        print("Only local file preparation is available; runtime start requires root GO.", file=sys.stderr)
        return 2
    try:
        prepare()
        print("Synthetic authority files prepared or verified.")
        return 0
    except Exception:
        print("Synthetic authority preparation refused; preserve existing state.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
