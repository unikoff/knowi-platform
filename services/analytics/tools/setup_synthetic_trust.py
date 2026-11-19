"""Prepare or verify separate synthetic signing keys without displaying them."""
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat, load_pem_private_key,
)
from pydantic import SecretStr

from setup_local_postgres import create_exclusive, ensure_directory, file_exists, reject_pending

SERVICE = Path(__file__).resolve().parents[1]
LOCAL = SERVICE / ".local"
sys.path.insert(0, str(SERVICE))
from pymentor_analytics.adapters.auth.config import load_manifest


def manifest_for(keys):
    public = {name: key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
              for name, key in keys.items()}
    return {"grant": {"issuer": "synthetic-grants", "keys": {"g1": public["grant"]}},
            "producer": {"issuer": "synthetic-control", "keys": {"p1": public["producer"]},
                         "allowlist": ["synthetic-producer"]}}


def setup(folder=LOCAL):
    ensure_directory(folder)
    paths = {name: folder / f"synthetic-{name}-private.pem" for name in ("grant", "producer")}
    manifest_path = folder / "trust-manifest.json"
    reject_pending(folder, (*paths.values(), manifest_path))
    present = [file_exists(paths[name]) for name in ("grant", "producer")] + [file_exists(manifest_path)]
    if any(present) and not all(present):
        raise ValueError("partial trust state; preserve and reconcile")
    if all(present):
        keys = {name: load_pem_private_key(path.read_bytes(), password=None) for name, path in paths.items()}
        if not all(isinstance(key, Ed25519PrivateKey) for key in keys.values()):
            raise ValueError("invalid signing key")
        content = manifest_path.read_text(encoding="utf-8")
        load_manifest(SecretStr(content))
        if json.loads(content) != manifest_for(keys):
            raise ValueError("public manifest does not match signing keys")
        return
    keys = {name: Ed25519PrivateKey.generate() for name in paths}
    content = json.dumps(manifest_for(keys), sort_keys=True)
    load_manifest(SecretStr(content))
    for name, key in keys.items():
        data = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        create_exclusive(paths[name], data)
    create_exclusive(manifest_path, (content + "\n").encode("utf-8"))


if __name__ == "__main__":
    try:
        setup()
        print("Synthetic signing configuration prepared or verified.")
    except Exception:
        print("Synthetic trust setup refused; preserve existing files and reconcile.", file=sys.stderr)
        raise SystemExit(2) from None
