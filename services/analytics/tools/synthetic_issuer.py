"""Explicit local-only synthetic key generation and token issuance; never production."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pymentor_analytics.contracts.v2 import GrantClaims, Identifier
from pymentor_analytics.contracts.producer_v1 import ProducerClaims
from pydantic import TypeAdapter

LOCAL_ROOT = Path(__file__).resolve().parents[1] / ".local"


def local_path(path: str) -> Path:
    root = LOCAL_ROOT.resolve()
    candidate = Path(path).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("key paths must be under Analytics .local")
    return candidate


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)
    issue = commands.add_parser("issue")
    issue.add_argument("--private-key", required=True)
    issue.add_argument("--kid", required=True)
    issue.add_argument("--claims-file", required=True)
    issue.add_argument("--kind", choices=("grant", "producer"), required=True)
    issue.add_argument("--emit-token", action="store_true", required=True)
    args = parser.parse_args()
    try:
        private_path = local_path(args.private_key)
        if args.command == "keygen":
            public_path = local_path(args.public_key)
            if private_path == public_path or private_path.exists() or public_path.exists():
                raise ValueError("output already exists")
            key = Ed25519PrivateKey.generate()
            private_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            public_bytes = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            write_new(private_path, private_bytes)
            write_new(public_path, public_bytes)
            print("synthetic key pair created")
            return
        TypeAdapter(Identifier).validate_python(args.kid, strict=True)
        raw = Path(args.claims_file).read_bytes()
        claims = (GrantClaims if args.kind == "grant" else ProducerClaims).model_validate_json(raw)
        now = time.time()
        max_ttl = 300 if args.kind == "grant" and claims.scope == "collect" else 60
        if not claims.iss.startswith("synthetic-") or not claims.nbf <= now < claims.exp or claims.iat > now or claims.exp - claims.iat > max_ttl:
            raise ValueError("invalid synthetic claims")
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Ed25519 key required")
        print(jwt.encode(json.loads(raw), key, algorithm="EdDSA", headers={"typ": "JWT", "kid": args.kid}))
    except Exception:
        raise SystemExit("synthetic issuer failed") from None


if __name__ == "__main__":
    main()
