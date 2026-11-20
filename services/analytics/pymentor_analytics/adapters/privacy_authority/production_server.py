"""Explicit production authority service. Never initializes state while serving."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import ssl
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pymentor_analytics.adapters.privacy_authority.client import _unique
from pymentor_analytics.adapters.privacy_authority.production_journal import ProductionJournal
from pymentor_analytics.adapters.privacy_authority.postgres_anchor import PostgresAnchorJournal
from pymentor_analytics.adapters.privacy_authority.server import BoundedFixtureProtocol, MAX_CONNECTIONS, create_authority_app

_FIELDS = {"mode", "fixture_mode", "authority_id", "issuer", "kid", "bind_host", "port", "journal_path", "anchor_path",
           "signing_key_file", "producer_issuer", "producer_public_keys", "allowed_producers", "producer_command_public_keys",
           "reader_secret_file", "tls_cert_file", "tls_key_file", "tls_client_ca_file"}


def _read(path, limit):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("absolute file path required")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file too large")
    return data


def load_config(path):
    cfg = json.loads(_read(path, 128 * 1024), object_pairs_hook=_unique)
    remote_fields = (_FIELDS - {"anchor_path"}) | {"anchor_backend", "anchor_dsn_file", "anchor_ca_file", "anchor_host", "anchor_database", "anchor_user"}
    if not isinstance(cfg, dict) or set(cfg) not in (_FIELDS, remote_fields) or (set(cfg) == remote_fields and cfg["anchor_backend"] != "postgres") or cfg["mode"] != "production" or cfg["fixture_mode"] is not False:
        raise ValueError("explicit production configuration required")
    if cfg["issuer"] != cfg["authority_id"] or not isinstance(cfg["bind_host"], str) or not cfg["bind_host"] or type(cfg["port"]) is not int or not 1 <= cfg["port"] <= 65535:
        raise ValueError("invalid authority identity or bind")
    for name in (("journal_path", "signing_key_file", "reader_secret_file", "tls_cert_file", "tls_key_file", "tls_client_ca_file") + (("anchor_dsn_file", "anchor_ca_file") if "anchor_backend" in cfg else ("anchor_path",))):
        if not isinstance(cfg[name], str) or not Path(cfg[name]).is_absolute():
            raise ValueError("absolute configured paths required")
    if (not isinstance(cfg["producer_public_keys"], dict) or not cfg["producer_public_keys"]
            or not isinstance(cfg["producer_command_public_keys"], dict)
            or not isinstance(cfg["allowed_producers"], list) or not cfg["allowed_producers"]
            or any(not isinstance(x, str) for x in cfg["allowed_producers"])
            or len(set(cfg["allowed_producers"])) != len(cfg["allowed_producers"])):
        raise ValueError("explicit producer scope required")
    return cfg


def tls_context(cfg):
    # Validate all material before opening a listener; Uvicorn builds the equivalent context.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=cfg["tls_client_ca_file"])
    context.load_cert_chain(certfile=cfg["tls_cert_file"], keyfile=cfg["tls_key_file"])
    if not context.get_ca_certs():
        raise ValueError("client trust store empty")
    return context


def _journal(cfg, *, initialize=False):
    if cfg.get("anchor_backend") == "postgres":
        args = {"dsn": _read(cfg["anchor_dsn_file"], 8192).decode("utf-8").strip(), "ca_file": cfg["anchor_ca_file"],
                "host": cfg["anchor_host"], "database": cfg["anchor_database"], "user": cfg["anchor_user"]}
        factory = PostgresAnchorJournal.initialize if initialize else PostgresAnchorJournal
        return factory(Path(cfg["journal_path"]), cfg["authority_id"], **args)
    factory = ProductionJournal.initialize if initialize else ProductionJournal
    return factory(Path(cfg["journal_path"]), Path(cfg["anchor_path"]), cfg["authority_id"])

def load_application(cfg, *, recovery_permit=None):
    tls_context(cfg)
    key = serialization.load_pem_private_key(_read(cfg["signing_key_file"], 8192), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Ed25519 signing key required")
    secret = _read(cfg["reader_secret_file"], 256).decode("ascii").strip()
    journal = _journal(cfg)
    return create_authority_app(journal, issuer=cfg["issuer"], kid=cfg["kid"], signing_key=key,
        producer_issuer=cfg["producer_issuer"], producer_public_keys={k: v.encode("ascii") for k, v in cfg["producer_public_keys"].items()},
        allowed_producers=set(cfg["allowed_producers"]), producer_command_public_keys={k: v.encode("ascii") for k, v in cfg["producer_command_public_keys"].items()},
        reader_secret=secret, recovery_permit=recovery_permit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "serve"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--allow-local-anchor-fixture", action="store_true", help="synthetic loopback fixture only")
    parser.add_argument("--host-boot-recovery-gate", action="store_true")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        if cfg.get("anchor_backend") != "postgres" and not (args.allow_local_anchor_fixture and cfg["bind_host"] in {"127.0.0.1", "::1"}):
            raise ValueError("independent PostgreSQL anchor required")
        if args.action == "init":
            _journal(cfg, initialize=True)
            print("authority journal initialized")
            return
        from pymentor_analytics.app.recovery_permit import host_boot_permitted
        app = load_application(cfg, recovery_permit=host_boot_permitted if args.host_boot_recovery_gate else None)
        import uvicorn
        uvicorn.run(app, host=cfg["bind_host"], port=cfg["port"], http=BoundedFixtureProtocol, ws="none",
            backlog=16, limit_concurrency=MAX_CONNECTIONS, timeout_keep_alive=2, h11_max_incomplete_event_size=8192,
            proxy_headers=False, access_log=False, log_config=None,
            ssl_certfile=cfg["tls_cert_file"], ssl_keyfile=cfg["tls_key_file"], ssl_ca_certs=cfg["tls_client_ca_file"],
            ssl_cert_reqs=ssl.CERT_REQUIRED, ssl_version=ssl.PROTOCOL_TLS_SERVER)
    except Exception:
        print("production authority unavailable; preserve state for reconciliation", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
