"""Pinned signed authority responses over isolated HTTP or mutually authenticated TLS."""
from __future__ import annotations

import asyncio
import base64
from collections import deque
import ipaddress
import json
import re
import ssl
import time
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ConfigDict, Field, SecretStr, TypeAdapter, field_validator, model_validator

from pymentor_analytics.contracts.privacy_control_v1 import (AppendReceipt, AuthorityConflict, AuthorityFrame, AuthorityProtocolError,
    AuthorityUnavailable, FRAME_TTL_SECONDS, MAX_ENTRIES, MAX_FRAME_BYTES, MAX_SEQUENCE, VerifiedFrame, canonical_json,
    deletion_command_bytes, deletion_hash)
from pymentor_analytics.contracts.v2 import Contract, DeletionFence, Identifier

_ALLOWED_NAMES = {"localhost", "privacy-authority", "privacy-authority.knowi-analytics.svc", "privacy-authority.knowi-analytics.svc.cluster.local"}
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_DOMAIN = b"pymentor:privacy-authority-frame:v1\0"


def _unb64(value: str) -> bytes:
    if not isinstance(value, str) or not _B64.fullmatch(value):
        raise ValueError("invalid encoding")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise ValueError("invalid encoding")
    return raw


def _address(url: str, *, runtime_mode: str = "synthetic"):
    if not isinstance(url, str) or not url or any(ord(character) <= 32 or ord(character) == 127 for character in url):
        raise ValueError("invalid fixture URL")
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != ("https" if runtime_mode == "production" else "http") or not host or parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port is None:
        raise ValueError("invalid fixture URL")
    host = host.lower()
    if runtime_mode not in {"synthetic", "local", "production"}:
        raise ValueError("invalid authority mode")
    if runtime_mode == "production":
        if not host.isascii() or "%" in host or not 1 <= parsed.port <= 65535:
            raise ValueError("invalid authority URL")
        return host, parsed.port
    try:
        address = ipaddress.ip_address(host)
        local = address.is_loopback and (runtime_mode == "synthetic" or "%" not in host)
    except ValueError:
        local = runtime_mode == "synthetic" and host in _ALLOWED_NAMES
    if not local or not 1 <= parsed.port <= 65535:
        raise ValueError("invalid fixture URL")
    return host, parsed.port


class AuthorityConfig(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    url: str
    authority_id: Identifier
    issuer: Identifier
    public_keys: dict[str, str] = Field(min_length=1, max_length=32, repr=False)
    reader_secret: SecretStr
    fixture_mode: bool
    runtime_mode: Literal["synthetic", "local", "production"] = "synthetic"
    tls_ca_file: str | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None

    @field_validator("fixture_mode", mode="before")
    @classmethod
    def fixture_only(cls, value):
        if type(value) is not bool:
            raise ValueError("explicit boolean fixture mode required")
        return value

    @model_validator(mode="after")
    def valid_config(self):
        if (self.runtime_mode == "synthetic") != self.fixture_mode:
            raise ValueError("authority mode and fixture mode differ")
        _address(self.url, runtime_mode=self.runtime_mode)
        tls_files = (self.tls_ca_file, self.tls_cert_file, self.tls_key_file)
        if self.runtime_mode == "production":
            if any(not isinstance(path, str) or not path.strip() or "\x00" in path for path in tls_files):
                raise ValueError("complete authority TLS configuration required")
        elif any(path is not None for path in tls_files):
            raise ValueError("TLS configuration requires production mode")
        if len(_unb64(self.reader_secret.get_secret_value())) != 32:
            raise ValueError("invalid reader credential")
        for kid, pem in self.public_keys.items():
            TypeAdapter(Identifier).validate_python(kid, strict=True)
            if len(pem.encode("ascii")) > 4096 or not isinstance(serialization.load_pem_public_key(pem.encode("ascii")), Ed25519PublicKey):
                raise ValueError("invalid authority key")
        return self

    @classmethod
    def from_json(cls, raw: str | bytes) -> "AuthorityConfig":
        try:
            if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > 128 * 1024:
                raise ValueError
            parsed = json.loads(raw, object_pairs_hook=_unique)
            return cls.model_validate_json(json.dumps(parsed))
        except Exception:
            raise ValueError("invalid authority configuration") from None


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate JSON key")
        data[key] = value
    return data


class AuthorityClient:
    def __init__(self, config: AuthorityConfig):
        self.config = config
        self.host, self.port = _address(config.url, runtime_mode=config.runtime_mode)
        self._tls = None
        if config.runtime_mode == "production":
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_verify_locations(cafile=config.tls_ca_file)
                context.load_cert_chain(certfile=config.tls_cert_file, keyfile=config.tls_key_file)
                self._tls = context
            except (OSError, ssl.SSLError, ValueError):
                raise ValueError("invalid authority TLS configuration") from None
        self._keys = MappingProxyType({kid: serialization.load_pem_public_key(pem.encode("ascii")) for kid, pem in config.public_keys.items()})
        self._sessions: dict[UUID, int] = {}
        self._hwm = 0
        self._nonces = set()
        self._recent = deque(maxlen=4096)
        self._inflight = asyncio.Lock()

    def _nonce(self):
        value = uuid4()
        if value in self._nonces:
            raise AuthorityProtocolError()
        if len(self._recent) == self._recent.maxlen:
            self._nonces.remove(self._recent.popleft())
        self._recent.append(value)
        self._nonces.add(value)
        return value

    async def _http(self, path: str, body: bytes, *, credential: str | None = None) -> bytes:
        if self._inflight.locked():
            raise AuthorityUnavailable()
        async with self._inflight:
            return await self._http_one(path, body, credential=credential)

    async def _http_one(self, path: str, body: bytes, *, credential: str | None = None) -> bytes:
        writer = None
        try:
            async with asyncio.timeout(FRAME_TTL_SECONDS):
                options = {"ssl": self._tls, "server_hostname": self.host} if self._tls is not None else {}
                reader, writer = await asyncio.open_connection(self.host, self.port, limit=16 * 1024, **options)
                credential = self.config.reader_secret.get_secret_value() if credential is None else credential
                host_header = self.host if self.config.runtime_mode == "production" else "fixture"
                headers = (f"POST {path} HTTP/1.1\r\nHost: {host_header}\r\nAuthorization: Bearer {credential}\r\n"
                           f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode("ascii")
                writer.write(headers + body)
                await writer.drain()
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head[:-4].split(b"\r\n")
                match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: .*)?", lines[0])
                if not match:
                    raise AuthorityProtocolError()
                status = int(match.group(1))
                response_headers = {}
                for line in lines[1:]:
                    name, sep, value = line.partition(b":")
                    name = name.strip().lower()
                    if not sep or name in response_headers:
                        raise AuthorityProtocolError()
                    response_headers[name] = value.strip()
                if status >= 500:
                    raise AuthorityUnavailable()
                if status == 409:
                    raise AuthorityConflict()
                if status != 200 or b"transfer-encoding" in response_headers:
                    raise AuthorityProtocolError()
                size = response_headers.get(b"content-length", b"")
                if not size.isdigit() or len(size) > 9 or int(size) > MAX_FRAME_BYTES:
                    raise AuthorityProtocolError()
                return await reader.readexactly(int(size))
        except (AuthorityUnavailable, AuthorityProtocolError, AuthorityConflict):
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            raise AuthorityUnavailable() from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=0.2)
                except (OSError, TimeoutError, ssl.SSLError):
                    pass

    async def _request(self, kind: str, *, session: UUID | None, after: int, through: int | None = None, limit: int = MAX_ENTRIES) -> VerifiedFrame:
        if type(after) is not int or not 0 <= after <= MAX_SEQUENCE or type(limit) is not int or not 1 <= limit <= MAX_ENTRIES:
            raise AuthorityProtocolError()
        if kind != "attach" and (not isinstance(session, UUID) or session not in self._sessions):
            raise AuthorityProtocolError()
        if kind == "page" and (type(through) is not int or not after <= through <= MAX_SEQUENCE):
            raise AuthorityProtocolError()
        nonce = self._nonce()
        request = {"nonce": str(nonce), "after": after, "session": str(session) if session else None,
                   "through": through, "limit": limit}
        started = time.monotonic()
        raw = await self._http("/v1/" + kind, canonical_json(request))
        try:
            envelope = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(envelope, dict) or set(envelope) != {"frame", "signature"} or not isinstance(envelope["frame"], dict):
                raise ValueError("invalid envelope")
            body = envelope["frame"]
            key = self._keys.get(body.get("kid"))
            if key is None:
                raise ValueError("unknown key")
            signature = _unb64(envelope["signature"])
            if len(signature) != 64:
                raise ValueError("invalid signature")
            key.verify(signature, _DOMAIN + canonical_json(body))
            frame = AuthorityFrame.model_validate_json(canonical_json(body))
            now = time.time()
            if (frame.kind != kind or frame.nonce != nonce or frame.authority_id != self.config.authority_id
                    or frame.issuer != self.config.issuer or frame.after != after or frame.through != through
                    or (session is not None and frame.session != session)
                    or (session is not None and session not in self._sessions)
                    or not frame.issued_at <= now < frame.expires_at or frame.high_watermark < self._hwm
                    or (kind == "page" and frame.scanned_through != min(through, after + limit))):
                raise ValueError("invalid signed frame")
            verified = VerifiedFrame(frame, started, time.monotonic())
            if not verified.is_fresh():
                raise ValueError("stale signed frame")
            self._hwm = frame.high_watermark
            if kind == "attach":
                self._sessions = {frame.session: after}
            return verified
        except (ValueError, TypeError, UnicodeError, InvalidSignature, RecursionError):
            raise AuthorityProtocolError() from None

    async def attach(self, after: int) -> VerifiedFrame:
        # A failed reattach must never leave a prior session usable.
        self._sessions.clear()
        return await self._request("attach", session=None, after=after)

    async def barrier(self, session: UUID) -> VerifiedFrame:
        if session not in self._sessions:
            raise AuthorityProtocolError()
        return await self._request("barrier", session=session, after=self._sessions[session])

    async def page(self, session: UUID, after: int, through: int, limit: int = MAX_ENTRIES) -> VerifiedFrame:
        return await self._request("page", session=session, after=after, through=through, limit=limit)

    async def observe(self, session: UUID, after: int) -> VerifiedFrame:
        return await self._request("observe", session=session, after=after)

    async def append(self, control: DeletionFence, *, producer: str, producer_token: str,
                     command_key: Ed25519PrivateKey) -> AppendReceipt:
        """Submit one signed synthetic command; receipt confirms journal only."""
        try:
            TypeAdapter(Identifier).validate_python(producer, strict=True)
            if not isinstance(control, DeletionFence) or not isinstance(command_key, Ed25519PrivateKey):
                raise ValueError
            if not isinstance(producer_token, str) or not producer_token.isascii() or not 1 <= len(producer_token) <= 16 * 1024 or any(c.isspace() for c in producer_token):
                raise ValueError
            nonce = self._nonce()
            signature = base64.urlsafe_b64encode(command_key.sign(deletion_command_bytes(producer, nonce, control))).rstrip(b"=").decode("ascii")
            body = canonical_json({"nonce": str(nonce), "control": control.model_dump(mode="json"), "signature": signature})
        except (TypeError, ValueError):
            raise AuthorityProtocolError() from None
        started = time.monotonic()
        raw = await self._http("/v1/append", body, credential=producer_token)
        try:
            envelope = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(envelope, dict) or set(envelope) != {"frame", "signature"} or not isinstance(envelope["frame"], dict):
                raise ValueError
            value = envelope["frame"]
            key = self._keys.get(value.get("kid"))
            if key is None:
                raise ValueError
            signature = _unb64(envelope["signature"])
            if len(signature) != 64:
                raise ValueError
            key.verify(signature, _DOMAIN + canonical_json(value))
            receipt = AppendReceipt.model_validate_json(canonical_json(value))
            now = time.time()
            if (receipt.nonce != nonce or receipt.authority_id != self.config.authority_id
                    or receipt.issuer != self.config.issuer or receipt.control_id != control.control_id
                    or receipt.producer != producer or receipt.payload_hash != deletion_hash(producer, control)
                    or not receipt.issued_at <= now < receipt.expires_at or time.monotonic() - started >= FRAME_TTL_SECONDS):
                raise ValueError
            self._hwm = max(self._hwm, receipt.seq)
            return receipt
        except (ValueError, TypeError, UnicodeError, InvalidSignature, RecursionError):
            raise AuthorityProtocolError() from None
