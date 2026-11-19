"""Authenticated, fresh, bounded snapshots from the local Core deletion source."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from pymentor_analytics.contracts.privacy_control_v1 import canonical_json, deletion_hash
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.v2 import DeletionFence

REQUEST_DOMAIN = b"pymentor:analytics:core-deletions:request:v1\0"
RESPONSE_DOMAIN = b"pymentor:analytics:core-deletions:response:v1\0"
MAX_BYTES = 256 * 1024
MAX_EVENTS = 1024
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError("duplicate key")
        value[key] = item
    return value


def _uuid(value):
    if not isinstance(value, str): raise ValueError("invalid UUID")
    parsed = UUID(value)
    if str(parsed) != value: raise ValueError("noncanonical UUID")
    return parsed


def _timestamp(value):
    if not isinstance(value, str) or not _TIME.fullmatch(value): raise ValueError("invalid timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _secret(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value): raise ValueError("invalid secret")
    raw = base64.urlsafe_b64decode(value + "=")
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value: raise ValueError("invalid secret")
    return raw


@dataclass(frozen=True, repr=False)
class SourceConfig:
    url: str
    instance_id: UUID
    secret: bytes

    @classmethod
    def from_json(cls, raw):
        try:
            if len(raw.encode("utf-8")) > 4096: raise ValueError
            value = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(value, dict) or set(value) != {"url", "instance_id", "secret"}: raise ValueError
            parsed = urlsplit(value["url"])
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 18041
                    or parsed.path != "/api/internal/analytics/deletions" or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment): raise ValueError
            return cls(value["url"], _uuid(value["instance_id"]), _secret(value["secret"]))
        except Exception:
            raise ValueError("invalid Core deletion source configuration") from None


def deletion_control(instance, subject, deleted_at):
    return DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid5(instance, "analytics-local-delete:" + str(subject)),
                         subject_ref=subject, epoch=1, occurred_at=deleted_at)


class CoreDeletionClient:
    def __init__(self, config):
        self.config = config

    async def snapshot(self):
        nonce = uuid4()
        request = {"version": 1, "instance_id": str(self.config.instance_id), "nonce": str(nonce)}
        signature = hmac.new(self.config.secret, REQUEST_DOMAIN + canonical_json(request), hashlib.sha256).hexdigest()
        writer = None
        started = time.monotonic()
        try:
            async with asyncio.timeout(1):
                reader, writer = await asyncio.open_connection("127.0.0.1", 18041, limit=16 * 1024)
                target = f"/api/internal/analytics/deletions?nonce={nonce}&instance_id={self.config.instance_id}"
                writer.write((f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:18041\r\nX-Analytics-Request-MAC: {signature}\r\nCache-Control: no-cache, no-store\r\nConnection: close\r\n\r\n").encode("ascii"))
                await writer.drain()
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header[:-4].split(b"\r\n")
                if not re.fullmatch(rb"HTTP/1\.[01] 200(?: .*)?", lines[0]): raise ValueError
                headers = {}
                for line in lines[1:]:
                    name, sep, val = line.partition(b":")
                    name = name.strip().lower()
                    if not sep or not name or name in headers: raise ValueError
                    headers[name] = val.strip()
                size = headers.get(b"content-length", b"")
                if b"transfer-encoding" in headers or not size.isdigit() or len(size) > 6 or int(size) > MAX_BYTES: raise ValueError
                raw = await reader.readexactly(int(size))
            if time.monotonic() - started >= 1: raise ValueError
            return self.validate(raw, nonce)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, TypeError, UnicodeError, RecursionError):
            raise PrivacyUnavailable() from None
        finally:
            if writer is not None:
                writer.close()
                try: await asyncio.wait_for(writer.wait_closed(), timeout=.2)
                except (OSError, TimeoutError): pass

    def validate(self, raw, nonce):
        try:
            if not isinstance(raw, bytes) or len(raw) > MAX_BYTES: raise ValueError
            body = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(body, dict) or set(body) != {"version", "instance_id", "nonce", "as_of", "events", "mac"}: raise ValueError
            mac = body.pop("mac")
            if not isinstance(mac, str) or not _HEX.fullmatch(mac) or not hmac.compare_digest(mac, hmac.new(self.config.secret, RESPONSE_DOMAIN + canonical_json(body), hashlib.sha256).hexdigest()): raise ValueError
            if type(body["version"]) is not int or body["version"] != 1 or _uuid(body["instance_id"]) != self.config.instance_id or _uuid(body["nonce"]) != nonce: raise ValueError
            now = datetime.now(timezone.utc)
            as_of = _timestamp(body["as_of"])
            if not -1 <= (now - as_of).total_seconds() <= 3: raise ValueError
            if not isinstance(body["events"], list) or len(body["events"]) > MAX_EVENTS: raise ValueError
            events = []
            for event in body["events"]:
                if not isinstance(event, dict) or set(event) != {"subject_ref", "deleted_at"}: raise ValueError
                subject, occurred = _uuid(event["subject_ref"]), _timestamp(event["deleted_at"])
                if occurred > as_of: raise ValueError
                events.append((subject, occurred))
            if [str(e[0]) for e in events] != sorted(set(str(e[0]) for e in events)): raise ValueError
            return tuple(deletion_control(self.config.instance_id, subject, occurred) for subject, occurred in events)
        except (ValueError, TypeError, UnicodeError, KeyError, OverflowError, RecursionError):
            raise PrivacyUnavailable() from None
