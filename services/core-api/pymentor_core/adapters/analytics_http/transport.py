"""Bounded, injectable Core transfer HTTP client with no implicit retries."""
from __future__ import annotations

import json
from time import monotonic
from urllib.parse import urlencode, urlsplit

from pymentor_core.adapters.analytics_http.http import request
from pymentor_core.modules.analytics_delivery.contracts import fingerprint
from pymentor_core.modules.analytics_delivery.relay import Receipt


class TransferHttp:
    def __init__(self, endpoint: str, producer_token, *, transport=None):
        url = urlsplit(endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("invalid transfer endpoint")
        self._endpoint = endpoint.rstrip("/")
        self._producer_token = producer_token
        self._transport = transport

    async def _request(self, path, *, deadline_seconds, body=None):
        if deadline_seconds <= 0:
            return Receipt.UNKNOWN
        started = monotonic()
        token = self._producer_token()
        remaining = deadline_seconds - (monotonic() - started)
        if remaining <= 0:
            return Receipt.UNKNOWN
        result = await request("POST" if body is not None else "GET", self._endpoint + path,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"},
            body=body, deadline_seconds=remaining, max_bytes=4096, transport=self._transport)
        if result is None:
            return Receipt.UNKNOWN
        try:
            status, raw = result
            if status != 200:
                return Receipt.UNKNOWN
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"status"} or not isinstance(value["status"], str):
                return Receipt.UNKNOWN
            return {"accepted": Receipt.ACCEPTED, "absent": Receipt.ABSENT,
                    "conflict": Receipt.CONFLICT}.get(value["status"], Receipt.UNKNOWN)
        except (ValueError, UnicodeError):
            return Receipt.UNKNOWN

    async def lookup(self, event_id: str, digest: str, deadline_seconds: float) -> Receipt:
        from uuid import UUID
        if str(UUID(event_id)) != event_id or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid receipt identity")
        return await self._request("/internal/core-transfer/v1/receipts/" + event_id + "?" + urlencode({"fingerprint": digest}),
                                   deadline_seconds=deadline_seconds)

    async def send(self, event: dict, deadline_seconds: float) -> Receipt:
        started = monotonic()
        body = json.dumps({"event": event, "fingerprint": fingerprint(event)},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > 8192:
            raise ValueError("transfer event too large")
        return await self._request("/internal/core-transfer/v1/events", deadline_seconds=deadline_seconds - (monotonic() - started), body=body)

    async def deletion_status(self, event: dict, deadline_seconds: float) -> Receipt:
        from uuid import UUID
        event_id, subject, epoch = event["event_id"], event["subject_ref"], event["revision"]
        if str(UUID(event_id)) != event_id or str(UUID(subject)) != subject or type(epoch) is not int or epoch < 1:
            raise ValueError("invalid deletion identity")
        return await self._request("/internal/core-transfer/v1/deletions/" + event_id + "?" +
                             urlencode({"subject_ref": subject, "epoch": epoch}), deadline_seconds=deadline_seconds)

    async def send_proof(self, proof: dict, deadline_seconds: float) -> Receipt:
        from pymentor_core.modules.analytics_delivery.snapshot_contract import proof_fingerprint, validate_proof
        validate_proof(proof)
        body = json.dumps({"proof": proof, "fingerprint": proof_fingerprint(proof)},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > 8192:
            raise ValueError("proof too large")
        return await self._request("/internal/core-transfer/v1/proofs", deadline_seconds=deadline_seconds, body=body)

    async def lookup_proof(self, proof_id: str, digest: str, deadline_seconds: float):
        from uuid import UUID
        if str(UUID(proof_id)) != proof_id or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid proof identity")
        if deadline_seconds <= 0:
            return Receipt.UNKNOWN, None
        started = monotonic()
        token = self._producer_token()
        remaining = deadline_seconds - (monotonic() - started)
        if remaining <= 0:
            return Receipt.UNKNOWN, None
        result = await request("GET", self._endpoint + "/internal/core-transfer/v1/proofs/" + proof_id + "?" + urlencode({"fingerprint": digest}),
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, body=None,
            deadline_seconds=remaining, max_bytes=4096, transport=self._transport)
        if result is None or result[0] != 200:
            return Receipt.UNKNOWN, None
        try:
            value = json.loads(result[1])
            if not isinstance(value, dict) or set(value) != {"status", "coverage"}:
                raise ValueError
            status, coverage = value["status"], value["coverage"]
            if status == "accepted" and coverage in {"pending", "verified", "unavailable"}:
                return Receipt.ACCEPTED, coverage
            if status in {"absent", "conflict"} and coverage is None:
                return (Receipt.ABSENT if status == "absent" else Receipt.CONFLICT), None
        except (ValueError, TypeError, UnicodeError):
            pass
        return Receipt.UNKNOWN, None
