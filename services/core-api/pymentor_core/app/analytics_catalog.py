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
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pymentor_core.adapters.analytics_catalog import publication
from pymentor_core.adapters.analytics_http.http import request
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
        path = "/internal/analytics/catalog/v1/receipts/" + quote(course, safe="") + "/" + quote(message["catalog_version"], safe="") + "?" + urlencode({"fingerprint": fingerprint})

        async def call(method, route, body=None):
            result = await request(method, binding.endpoint + route, headers={"Authorization": "Bearer " + token(),
                "Accept": "application/json", "Content-Type": "application/json"}, body=body,
                deadline_seconds=5, max_bytes=4096, transport=transport)
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
            sent = await call("POST", "/internal/analytics/catalog/v1", publication.canonical(
                {"message": message, "fingerprint": fingerprint}))
            if sent in {"accepted", "unknown"}:
                status = await call("GET", path)
            else:
                status = sent
        if status != "accepted":
            return status
        publication.write_receipt(course, receipt, root=root)
        publication.clear_pending(course, root=root)
        return "accepted" if publication.current_digest(snapshot) == source_digest else "changed"


def main():
    parser = argparse.ArgumentParser(description="Publish one catalog to an explicitly bound local Analytics dataset")
    parser.add_argument("--course-id", required=True)
    args = parser.parse_args()
    env = os.environ
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
        raise SystemExit("Local catalog publication unavailable") from None
