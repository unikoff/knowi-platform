"""Append-only authority client. A receipt proves journal append, not purge."""
from __future__ import annotations

import base64
from datetime import timezone
from hashlib import sha256
import json
import time
from urllib.parse import urlsplit
from uuid import uuid4

from pymentor_core.adapters.analytics_http.http import request
from pymentor_core.modules.analytics_delivery.errors import AuthorityConflict

_FRAME_DOMAIN = b"pymentor:privacy-authority-frame:v1\0"
_COMMAND_DOMAIN = b"pymentor:privacy-authority-command:v1\0"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate member")
        result[key] = value
    return result


class AuthorityClient:
    def __init__(self, *, endpoint, authority_id, issuer, public_keys, producer, producer_token, command_key, transport=None):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        url = urlsplit(endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("invalid authority endpoint")
        if not isinstance(command_key, Ed25519PrivateKey) or not authority_id or not issuer or not producer or not public_keys:
            raise ValueError("invalid authority configuration")
        self._keys = {kid: load_pem_public_key(pem) for kid, pem in public_keys.items()}
        if any(not isinstance(key, Ed25519PublicKey) for key in self._keys.values()):
            raise ValueError("invalid authority key")
        self._endpoint, self._authority_id, self._issuer = endpoint.rstrip("/"), authority_id, issuer
        self._producer, self._token, self._command_key = producer, producer_token, command_key
        self._transport = transport

    async def append(self, event: dict, created_at, *, timeout_seconds: float) -> int | None:
        """Returns verified sequence on append; None means unknown outcome.

        A new nonce is used on retries; the control ID and timestamp stay stable.
        The caller MUST still prove applied prefix and purge before ACK.
        """
        if event.get("kind") != "subject_deleted" or timeout_seconds <= 0:
            raise ValueError("invalid deletion")
        started = time.monotonic()
        value = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        timestamp = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        control = {"schema_version": 2, "kind": "deletion_fence", "control_id": event["event_id"],
                   "subject_ref": event["subject_ref"], "epoch": event["revision"], "occurred_at": timestamp}
        nonce = str(uuid4())
        signature = self._command_key.sign(_COMMAND_DOMAIN + _canonical({"producer": self._producer, "nonce": nonce, "control": control}))
        body = _canonical({"nonce": nonce, "control": control, "signature": _b64(signature)})
        token = self._token()
        remaining = min(3, timeout_seconds) - (time.monotonic() - started)
        if remaining <= 0:
            return None
        result = await request("POST", self._endpoint + "/v1/append", body=body,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            deadline_seconds=remaining, max_bytes=131072, transport=self._transport)
        if result is None:
            return None
        status, raw = result
        if status == 409:
            raise AuthorityConflict()
        if status != 200:
            return None
        try:
            envelope = json.loads(raw, object_pairs_hook=_unique)
            if set(envelope) != {"frame", "signature"} or not isinstance(envelope["frame"], dict):
                return None
            frame = envelope["frame"]
            required = {"version", "kind", "authority_id", "issuer", "audience", "kid", "nonce", "control_id",
                        "producer", "payload_hash", "status", "seq", "issued_at", "expires_at"}
            sig = envelope["signature"]
            if set(frame) != required or not isinstance(sig, str):
                return None
            signature_bytes = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
            if _b64(signature_bytes) != sig or len(signature_bytes) != 64:
                return None
            self._keys[frame["kid"]].verify(signature_bytes, _FRAME_DOMAIN + _canonical(frame))
            now = int(time.time())
            expected_hash = sha256(_canonical({"producer": self._producer, "control": control})).hexdigest()
            if (type(frame["version"]) is not int or frame["version"] != 1 or frame["kind"] != "append"
                    or frame["authority_id"] != self._authority_id or frame["issuer"] != self._issuer
                    or frame["audience"] != "analytics-privacy-journal" or frame["nonce"] != nonce
                    or frame["control_id"] != control["control_id"] or frame["producer"] != self._producer
                    or frame["payload_hash"] != expected_hash or frame["status"] not in {"accepted", "duplicate"}
                    or type(frame["seq"]) is not int or not 1 <= frame["seq"] <= (1 << 63) - 1
                    or type(frame["issued_at"]) is not int or type(frame["expires_at"]) is not int
                    or not frame["issued_at"] <= now < frame["expires_at"] <= frame["issued_at"] + 3
                    or time.monotonic() - started >= min(3, timeout_seconds)):
                return None
            return frame["seq"]
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None
        except Exception:
            # Includes signature verification failures; never disclose key or body.
            return None
