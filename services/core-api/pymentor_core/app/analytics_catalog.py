"""Read and publish Analytics catalog receipts from the configured course source."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
from contextlib import contextmanager
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pymentor_core.adapters.analytics_catalog import publication
from pymentor_core.adapters.analytics_http.http import request
from pymentor_core.adapters.analytics_http.http import client_tls
from pymentor_core.adapters.course_local.runtime import default_runtime
from pymentor_core.modules.analytics_delivery.signing import Signer


@dataclass(frozen=True)
class Binding:
    dataset_id: str
    instance_id: str
    namespace: str
    endpoint: str
    producer_sha256: str
    producer_id: str
    producer_issuer: str
    producer_kid: str

    def value(self):
        return {"version": 1, "dataset_id": self.dataset_id, "instance_id": self.instance_id,
                "namespace": self.namespace, "endpoint": self.endpoint, "producer_sha256": self.producer_sha256,
                "producer_id": self.producer_id, "producer_issuer": self.producer_issuer, "producer_kid": self.producer_kid}

    def root(self):
        base = Path(publication.__file__).resolve().parents[3] / ".local" / "analytics-live" / "catalog"
        return base / self.dataset_id / sha256(self.namespace.encode("utf-8")).hexdigest()


def binding_from_env(env, instance_id):
    try:
        dataset = env["PYMENTOR_ANALYTICS_LOCAL_DATASET_ID"]
        if str(UUID(dataset)) != dataset or str(UUID(instance_id)) != instance_id:
            raise ValueError
        namespace = env["PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE"]
        endpoint = env["PYMENTOR_ANALYTICS_LOCAL_CATALOG_URL"]
        fingerprint = env["PYMENTOR_ANALYTICS_LOCAL_CATALOG_PRODUCER_KEY_SHA256"]
        producer = env["PYMENTOR_ANALYTICS_PRODUCER_ID"]
        issuer = env["PYMENTOR_ANALYTICS_PRODUCER_ISSUER"]
        kid = env["PYMENTOR_ANALYTICS_PRODUCER_KID"]
        parsed = urlsplit(endpoint)
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", namespace)
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item) for item in (producer, issuer, kid))
                or parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
                or parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError
        return Binding(dataset, instance_id, namespace, endpoint.rstrip("/"), fingerprint, producer, issuer, kid)
    except Exception:
        raise ValueError("Invalid local catalog binding") from None


def verified_root(binding):
    if not isinstance(binding, Binding):
        raise ValueError("Catalog binding unavailable")
    root = binding.root()
    publication.receipt_path("binding-check", root=root)
    marker = root / "binding.json"
    if marker.is_symlink() or os.path.isjunction(marker) or marker.stat().st_size > 4096:
        raise ValueError("Invalid local catalog binding")
    if marker.read_bytes() != publication.canonical(binding.value()):
        raise ValueError("Local catalog binding mismatch")
    return root


def read_published_catalog(course_id, local_collection=None):
    snapshot = default_runtime().snapshot(course_id)
    if local_collection is None:
        receipt = publication.read_receipt(course_id)
    else:
        receipt = publication.read_receipt(course_id, root=verified_root(local_collection.catalog_binding))
    if publication.current_digest(snapshot) != receipt["source_digest"]:
        raise ValueError("Catalog publication no longer matches the course")
    return snapshot, receipt["message"]


@dataclass(frozen=True)
class ProductionBinding:
    """Operator-provisioned identity and durable receipt directory.

    The directory and binding marker must be provisioned independently and shared
    by publisher and readers. Restores require an external quarantine/reconcile.
    """
    dataset_id: str
    instance_id: str
    namespace: str
    endpoint: str
    producer_sha256: str
    producer_id: str
    producer_issuer: str
    producer_kid: str
    directory: Path

    def value(self):
        return {"version": 1, "dataset_id": self.dataset_id, "instance_id": self.instance_id,
                "namespace": self.namespace, "endpoint": self.endpoint, "producer_sha256": self.producer_sha256,
                "producer_id": self.producer_id, "producer_issuer": self.producer_issuer, "producer_kid": self.producer_kid}


def production_binding_from_env(env):
    """Disabled unless explicitly enabled; malformed configuration fails closed."""
    enabled = env.get("PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_ENABLED", "0")
    if enabled == "0":
        return None
    if enabled != "1":
        raise ValueError("Invalid production catalog setting")
    try:
        prefix = "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_"
        dataset, instance = env[prefix + "DATASET_ID"], env[prefix + "INSTANCE_ID"]
        if str(UUID(dataset)) != dataset or str(UUID(instance)) != instance:
            raise ValueError
        namespace, endpoint = env[prefix + "NAMESPACE"], env[prefix + "URL"]
        fingerprint = env[prefix + "PRODUCER_KEY_SHA256"]
        producer, issuer, kid = (env["PYMENTOR_ANALYTICS_PRODUCER_" + name] for name in ("ID", "ISSUER", "KID"))
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item)
                       for item in (namespace, producer, issuer, kid))):
            raise ValueError
        directory = Path(env[prefix + "RECEIPT_DIR"])
        if not directory.is_absolute() or directory.resolve(strict=True) != directory or not directory.is_dir():
            raise ValueError
        return ProductionBinding(dataset, instance, namespace, endpoint.rstrip("/"), fingerprint,
                                 producer, issuer, kid, directory)
    except (KeyError, ValueError, TypeError, OSError):
        raise ValueError("Invalid production catalog binding") from None


def production_root(binding):
    if not isinstance(binding, ProductionBinding):
        raise ValueError("Production catalog binding unavailable")
    endpoint = urlsplit(binding.endpoint)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None
            or endpoint.password is not None or endpoint.path not in ("", "/")
            or endpoint.query or endpoint.fragment):
        raise ValueError("Invalid production catalog endpoint")
    root = binding.directory
    if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("Invalid production catalog directory")
    marker = root / "binding.json"
    if marker.is_symlink() or os.path.isjunction(marker) or marker.stat().st_size > 4096:
        raise ValueError("Invalid production catalog binding")
    if marker.read_bytes() != publication.canonical(binding.value()):
        raise ValueError("Production catalog binding mismatch")
    return root


def _production_path(binding, course_id, suffix=".json"):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", course_id):
        raise ValueError("invalid course")
    path = production_root(binding) / (sha256(course_id.encode("utf-8")).hexdigest() + suffix)
    if path.is_symlink() or os.path.isjunction(path):
        raise ValueError("invalid production catalog file")
    return path


def _production_read(binding, course_id, *, pending=False, suffix=None):
    path = _production_path(binding, course_id, suffix or (".pending.json" if pending else ".json"))
    if path.stat().st_size > publication.MAX_BYTES + 2048:
        raise ValueError("invalid receipt")
    receipt = json.loads(path.read_bytes())
    if not isinstance(receipt, dict) or set(receipt) != {"message", "fingerprint", "source_digest"}:
        raise ValueError("invalid receipt")
    message = receipt["message"]
    if (message["catalog"]["course_id"] != course_id
            or message["catalog_version"] != "sha256:" + publication.digest(message["catalog"])
            or receipt["fingerprint"] != publication.digest(message)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["source_digest"])
            or (suffix is not None and suffix.startswith(".source-")
                and suffix != _source_suffix(receipt["source_digest"]))):
        raise ValueError("invalid receipt")
    return receipt


def _production_write(binding, course_id, receipt, *, pending=False, suffix=None):
    path = _production_path(binding, course_id, suffix or (".pending.json" if pending else ".json"))
    fd, temp = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(publication.canonical(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _sync_directory(directory):
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def _production_lock(binding, course_id):
    path = _production_path(binding, course_id, ".lock")
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("publication already running") from None
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("publication already running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_production_catalog(course_id, binding):
    snapshot = default_runtime().snapshot(course_id)
    source_digest = publication.current_digest(snapshot)
    try:
        receipt = _production_read(binding, course_id, suffix=_source_suffix(source_digest))
    except FileNotFoundError:
        receipt = _production_read(binding, course_id)
    if source_digest != receipt["source_digest"]:
        raise ValueError("Catalog publication no longer matches the course")
    return snapshot, receipt["message"]


def _source_suffix(source_digest):
    if not re.fullmatch(r"[0-9a-f]{64}", source_digest):
        raise ValueError("invalid source digest")
    return ".source-" + source_digest + ".json"


def _write_source_receipt(binding, course, receipt):
    suffix = _source_suffix(receipt["source_digest"])
    try:
        existing = _production_read(binding, course, suffix=suffix)
    except FileNotFoundError:
        _production_write(binding, course, receipt, suffix=suffix)
    else:
        if existing != receipt:
            raise ValueError("source digest already has a different receipt")


def _versioned_head(binding, course):
    try:
        return _production_read(binding, course, suffix=".head.json")
    except FileNotFoundError:
        try:
            legacy = _production_read(binding, course)
        except FileNotFoundError:
            return None
        _write_source_receipt(binding, course, legacy)
        _production_write(binding, course, legacy, suffix=".head.json")
        return legacy


async def _reconcile(endpoint, message, fingerprint, token, *, transport=None, verify=None):
    course = message["catalog"]["course_id"]
    path = "/internal/analytics/catalog/v1/receipts/" + quote(course, safe="") + "/" + quote(message["catalog_version"], safe="") + "?" + urlencode({"fingerprint": fingerprint})

    async def call(method, route, body=None):
        options = {"headers": {"Authorization": "Bearer " + token(), "Accept": "application/json",
                               "Content-Type": "application/json"},
                   "body": body, "deadline_seconds": 5, "max_bytes": 4096, "transport": transport}
        if verify is not None:
            options["verify"] = verify
        result = await request(method, endpoint + route, **options)
        if result is None:
            return "unknown"
        try:
            status, raw = result
            value = json.loads(raw)
            if status not in (200, 409) or not isinstance(value, dict) or set(value) != {"status"}:
                return "unknown"
            return value["status"] if value["status"] in {"accepted", "absent", "conflict"} else "unknown"
        except (ValueError, TypeError):
            return "unknown"

    status = await call("GET", path)
    if status == "absent":
        status = await call("POST", "/internal/analytics/catalog/v1", publication.canonical(
            {"message": message, "fingerprint": fingerprint}))
        if status in {"accepted", "unknown"}:
            status = await call("GET", path)
    return status


async def publish_production(binding, snapshot, signer, env, *, transport=None, versioned=False):
    """Publish after operator restore quarantine; no runtime schedules this API."""
    production_root(binding)
    if signer.issuer != binding.producer_issuer or signer.kid != binding.producer_kid or signer._purpose != "producer":
        raise ValueError("Production producer identity mismatch")
    public = signer._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if sha256(public).hexdigest() != binding.producer_sha256:
        raise ValueError("Production producer fingerprint mismatch")
    tls = client_tls(env, "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_TLS", binding.endpoint)
    if not isinstance(tls, ssl.SSLContext) or not tls.check_hostname or tls.verify_mode != ssl.CERT_REQUIRED:
        raise ValueError("Production catalog mutual TLS required")
    course = snapshot.context.course_id
    with _production_lock(binding, course):
        if versioned:
            return await _publish_versioned(binding, snapshot, signer, tls, transport)
        if _production_path(binding, course, ".head.json").exists():
            raise ValueError("versioned catalog publication is active")
        try:
            pending = _production_read(binding, course, pending=True)
        except FileNotFoundError:
            pending = None
        if pending:
            message, source_digest = pending["message"], pending["source_digest"]
        else:
            try:
                prior = _production_read(binding, course)["message"]
            except FileNotFoundError:
                prior = None
            message, source_digest = publication.project(snapshot, prior)
        fingerprint = publication.digest(message)
        receipt = {"message": message, "fingerprint": fingerprint, "source_digest": source_digest}
        if pending is None:
            _production_write(binding, course, receipt, pending=True)
        status = await _reconcile(binding.endpoint, message, fingerprint,
            lambda: signer.producer(binding.producer_id), transport=transport, verify=tls)
        if status != "accepted":
            return status
        _production_write(binding, course, receipt)
        _production_path(binding, course, ".pending.json").unlink(missing_ok=True)
        _sync_directory(binding.directory)
        return "accepted" if publication.current_digest(snapshot) == source_digest else "changed"


async def _publish_versioned(binding, snapshot, signer, tls, transport):
    course = snapshot.context.course_id
    head = _versioned_head(binding, course)
    target_digest = publication.current_digest(snapshot)

    # One existing pending transaction and one new transaction are sufficient.
    for _ in range(2):
        try:
            pending = _production_read(binding, course, pending=True)
        except FileNotFoundError:
            pending = None
        if pending is None:
            try:
                existing = _production_read(binding, course, suffix=_source_suffix(target_digest))
            except FileNotFoundError:
                message, source_digest = publication.project(snapshot, head["message"] if head else None)
                pending = {"message": message, "fingerprint": publication.digest(message),
                           "source_digest": source_digest}
                _production_write(binding, course, pending, pending=True)
            else:
                status = await _reconcile(binding.endpoint, existing["message"], existing["fingerprint"],
                    lambda: signer.producer(binding.producer_id), transport=transport, verify=tls)
                if status != "accepted":
                    return status
                return "accepted" if publication.current_digest(snapshot) == target_digest else "changed"

        message = pending["message"]
        if head is not None:
            head_message = head["message"]
            if (message["sequence"] < head_message["sequence"]
                    or (message["sequence"] == head_message["sequence"]
                        and message["catalog_version"] != head_message["catalog_version"])):
                raise ValueError("pending catalog precedes the publication head")
        status = await _reconcile(binding.endpoint, message, pending["fingerprint"],
            lambda: signer.producer(binding.producer_id), transport=transport, verify=tls)
        if status != "accepted":
            return status
        _write_source_receipt(binding, course, pending)
        _production_write(binding, course, pending, suffix=".head.json")
        _production_path(binding, course, ".pending.json").unlink()
        _sync_directory(binding.directory)
        head = pending
        if pending["source_digest"] == target_digest:
            return "accepted" if publication.current_digest(snapshot) == target_digest else "changed"
    return "changed"


def initialize_binding(binding):
    root = binding.root()
    publication.receipt_path("binding-check", root=root)
    root.mkdir(parents=True, exist_ok=True)
    publication.receipt_path("binding-check", root=root)
    marker = root / "binding.json"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return verified_root(binding)
    with os.fdopen(fd, "wb") as out:
        out.write(publication.canonical(binding.value()))
        out.flush()
        os.fsync(out.fileno())
    return verified_root(binding)


async def publish(binding, snapshot, token, *, transport=None):
    root = initialize_binding(binding)
    course = snapshot.context.course_id
    with publication.publication_lock(course, root=root):
        try:
            pending = publication.read_receipt(course, pending=True, root=root)
        except FileNotFoundError:
            pending = None
        if pending:
            message, source_digest = pending["message"], pending["source_digest"]
        else:
            try:
                prior = publication.read_receipt(course, root=root)["message"]
            except FileNotFoundError:
                prior = None
            message, source_digest = publication.project(snapshot, prior)
        fingerprint = publication.digest(message)
        receipt = {"message": message, "fingerprint": fingerprint, "source_digest": source_digest}
        if pending is None:
            publication.write_receipt(course, receipt, pending=True, root=root)
        status = await _reconcile(binding.endpoint, message, fingerprint, token, transport=transport)
        if status != "accepted":
            return status
        publication.write_receipt(course, receipt, root=root)
        publication.clear_pending(course, root=root)
        return "accepted" if publication.current_digest(snapshot) == source_digest else "changed"


def main():
    parser = argparse.ArgumentParser(description="Publish one catalog to an explicitly bound Analytics dataset")
    parser.add_argument("--production", action="store_true", help="publish after the operator has completed restore quarantine")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--versioned", action="store_true", help="retain receipts for concurrently running course versions")
    args = parser.parse_args()
    env = os.environ
    if args.versioned and not args.production:
        raise ValueError("Versioned publication requires production configuration")
    if args.production:
        binding = production_binding_from_env(env)
        if binding is None:
            raise ValueError("Production Analytics catalog publication is disabled")
        production_root(binding)
        signer = Signer(issuer=binding.producer_issuer, kid=binding.producer_kid,
            private_pem=Path(env["PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE"]).read_bytes(), purpose="producer")
        snapshot = default_runtime().snapshot(args.course_id)
        if args.versioned:
            status = asyncio.run(publish_production(binding, snapshot, signer, env, versioned=True))
        else:
            status = asyncio.run(publish_production(binding, snapshot, signer, env))
        print(status)
        if status != "accepted":
            raise SystemExit(1)
        return
    if env.get("PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED") != "1":
        raise ValueError("Local Analytics collection is disabled")
    binding = binding_from_env(env, env["PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID"])
    signer = Signer(issuer=env["PYMENTOR_ANALYTICS_PRODUCER_ISSUER"], kid=env["PYMENTOR_ANALYTICS_PRODUCER_KID"],
        private_pem=Path(env["PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE"]).read_bytes(), purpose="producer")
    public = signer._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if sha256(public).hexdigest() != binding.producer_sha256:
        raise ValueError("Local producer fingerprint mismatch")
    snapshot = default_runtime().snapshot(args.course_id)
    status = asyncio.run(publish(binding, snapshot, lambda: signer.producer(env["PYMENTOR_ANALYTICS_PRODUCER_ID"])))
    print(status)
    if status != "accepted":
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Catalog publication unavailable") from None
