"""Validation and durable publication of subject-free catalog images."""
import hashlib
import hmac
import json
import re

from pymentor_analytics.contracts.catalog_v1 import Delta, Snapshot

MAX_BODY_BYTES = 2 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def parse_message(value):
    if not isinstance(value, dict):
        raise ValueError("invalid catalog")
    cls = {"analytics_catalog_snapshot": Snapshot, "analytics_catalog_delta": Delta}.get(value.get("kind"))
    if cls is None:
        raise ValueError("invalid catalog kind")
    # JSON validation accepts the tuple arrays in the immutable v1 contract.
    message = cls.model_validate_json(canonical(value))
    if message.sequence > 9223372036854775807:
        raise ValueError("catalog sequence exceeds storage limit")
    image = message.catalog.model_dump(mode="json")
    if message.catalog_version != "sha256:" + digest(image):
        raise ValueError("catalog version does not identify image")
    if len(message.catalog.tracks) > 100 or len(message.catalog.lessons) > 10000 or sum(len(x.tasks) for x in message.catalog.lessons) > 50000:
        raise ValueError("catalog exceeds item limits")
    return message


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class CatalogService:
    def __init__(self, store):
        self.store = store

    async def accept(self, namespace, value, fingerprint, *, deadline):
        message = parse_message(value)
        if not valid_digest(fingerprint) or not hmac.compare_digest(digest(message.model_dump(mode="json")), fingerprint):
            raise ValueError("invalid fingerprint")
        return await self.store.accept(namespace, message, fingerprint, deadline=deadline)

    async def receipt(self, namespace, course_id, catalog_version, fingerprint, *, deadline):
        if not valid_digest(fingerprint) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", course_id) or not re.fullmatch(r"sha256:[0-9a-f]{64}", catalog_version):
            raise ValueError("invalid receipt")
        return await self.store.receipt(namespace, course_id, catalog_version, fingerprint, deadline=deadline)
