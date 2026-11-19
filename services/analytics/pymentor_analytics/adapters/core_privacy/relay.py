"""Bounded retrying bridge from authenticated Core snapshots to the journal."""
import asyncio
from contextlib import suppress
import json
import time
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter
from pymentor_analytics.contracts.v2 import Identifier
from pymentor_analytics.contracts.privacy_control_v1 import deletion_hash


class RelayConfig:
    def __init__(self, producer, issuer, kid, signing_key, command_key):
        self.producer, self.issuer, self.kid = producer, issuer, kid
        self.signing_key, self.command_key = signing_key, command_key

    @classmethod
    def from_json(cls, raw):
        try:
            if len(raw.encode("utf-8")) > 16384: raise ValueError
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result: raise ValueError
                    result[key] = value
                return result
            v = json.loads(raw, object_pairs_hook=unique)
            if not isinstance(v, dict) or set(v) != {"producer", "producer_issuer", "producer_kid", "producer_private_key_pem", "command_private_key_pem"}: raise ValueError
            producer, issuer, kid = (TypeAdapter(Identifier).validate_python(v[key], strict=True) for key in ("producer", "producer_issuer", "producer_kid"))
            keys = [serialization.load_pem_private_key(v[key].encode("ascii"), password=None) for key in ("producer_private_key_pem", "command_private_key_pem")]
            if not all(isinstance(k, Ed25519PrivateKey) for k in keys): raise ValueError
            public = [k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) for k in keys]
            if public[0] == public[1]: raise ValueError
            return cls(producer, issuer, kid, *keys)
        except Exception:
            raise ValueError("invalid Core deletion relay configuration") from None

    def token(self):
        now = int(time.time())
        return jwt.encode({"grant_version": 1, "iss": self.issuer, "aud": "analytics-control", "sub": self.producer,
                           "iat": now, "nbf": now, "exp": now + 60, "scope": "control:write"},
                          self.signing_key, algorithm="EdDSA", headers={"typ": "JWT", "kid": self.kid})


class CoreDeletionRelay:
    def __init__(self, source, authority, config):
        self.source, self.authority, self.config = source, authority, config
        self._stop, self._task, self._cursor = asyncio.Event(), None, 0
        self._sent = {}

    def start(self):
        if self._task is not None or self._stop.is_set(): raise RuntimeError("relay already started or stopped")
        self._task = asyncio.create_task(self._run(), name="analytics-core-deletion-relay")

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError): await self._task
            self._task = None

    async def tick(self):
        controls = await self.source.snapshot()
        if not controls:
            self._cursor = 0
            return
        self._cursor %= len(controls)
        current = {c.control_id for c in controls}
        self._sent = {key: value for key, value in self._sent.items() if key in current}
        sent = 0
        for _ in range(len(controls)):
            control = controls[self._cursor]
            digest = deletion_hash(self.config.producer, control)
            previous = self._sent.get(control.control_id)
            if previous is not None and previous[0] != digest:
                raise ValueError("conflicting Core deletion identity")
            if previous is None or time.monotonic() - previous[1] >= 30:
                # Unknown outcomes are retried later with identical identity and payload.
                await self.authority.append(control, producer=self.config.producer,
                                            producer_token=self.config.token(), command_key=self.config.command_key)
                self._sent[control.control_id] = (digest, time.monotonic())
                sent += 1
            self._cursor = (self._cursor + 1) % len(controls)
            if sent >= 32:
                break

    async def _run(self):
        while not self._stop.is_set():
            try: await self.tick()
            except asyncio.CancelledError: raise
            except Exception: pass  # closed readers and next bounded retry; no personal data in logs
            try: await asyncio.wait_for(self._stop.wait(), timeout=1)
            except TimeoutError: pass
