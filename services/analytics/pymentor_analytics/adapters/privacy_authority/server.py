"""Private synthetic fixture authority; never mounted as a product API."""
from __future__ import annotations

import asyncio
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field, TypeAdapter
from uvicorn.protocols.http.h11_impl import H11Protocol

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.adapters.auth.verifier import ProducerVerifier
from pymentor_analytics.adapters.privacy_authority.client import _DOMAIN, _unb64, _unique
from pymentor_analytics.adapters.privacy_authority.journal import Journal, JournalConflict, JournalError
from pymentor_analytics.contracts.privacy_control_v1 import (AuthorityFrame, FRAME_TTL_SECONDS, MAX_FRAME_BYTES, MAX_SEQUENCE, canonical_json,
                                                               deletion_command_bytes, deletion_hash)
from pymentor_analytics.contracts.v2 import Contract, DeletionFence, Identifier

MAX_REQUEST_BYTES = 8192
MAX_SESSIONS = 128
MAX_NONCES = 8192
SESSION_TTL_SECONDS = 60
MAX_CONNECTIONS = 16
HEADER_TIMEOUT_SECONDS = 3


class BoundedFixtureProtocol(H11Protocol):
    """Uvicorn 0.50 h11: bound accepted sockets before complete headers."""
    def connection_made(self, transport):
        self._admitted = len(self.connections) < MAX_CONNECTIONS
        self._header_timer = None
        if not self._admitted:
            transport.close()
            return
        super().connection_made(transport)
        self._header_timer = self.loop.call_later(HEADER_TIMEOUT_SECONDS, self._expire_headers)

    def _expire_headers(self):
        if self.cycle is None and self.transport is not None:
            self.transport.close()

    def data_received(self, data):
        if not self._admitted:
            return
        super().data_received(data)
        if self.cycle is not None and self._header_timer is not None:
            self._header_timer.cancel()
            self._header_timer = None

    def connection_lost(self, exc):
        if self._header_timer is not None:
            self._header_timer.cancel()
            self._header_timer = None
        if self._admitted:
            super().connection_lost(exc)


class FrameRequest(Contract):
    nonce: UUID
    session: UUID | None
    after: int = Field(ge=0, le=MAX_SEQUENCE)
    through: int | None = Field(ge=0, le=MAX_SEQUENCE)
    limit: int = Field(ge=1, le=100)


class AppendRequest(Contract):
    nonce: UUID
    control: DeletionFence
    signature: str = Field(min_length=86, max_length=86)


def _error(status: int):
    return JSONResponse(status_code=status, content={"error": "authority request unavailable"})


async def _body(request: Request) -> bytes:
    lengths = [value for name, value in request.scope["headers"] if name.lower() == b"content-length"]
    if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 9 or int(lengths[0]) > MAX_REQUEST_BYTES)):
        raise ValueError("invalid or oversized request")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        data.extend(chunk)
    return bytes(data)


def create_authority_app(journal: Journal, *, issuer: str, kid: str, signing_key: Ed25519PrivateKey,
                         producer_issuer: str, producer_public_keys: dict[str, bytes],
                         allowed_producers: set[str], producer_command_public_keys: dict[str, bytes],
                         reader_secret: str) -> FastAPI:
    TypeAdapter(Identifier).validate_python(issuer, strict=True)
    TypeAdapter(Identifier).validate_python(kid, strict=True)
    if not isinstance(signing_key, Ed25519PrivateKey) or len(_unb64(reader_secret)) != 32:
        raise ValueError("invalid authority configuration")
    verifier = ProducerVerifier(issuer=producer_issuer, public_keys=producer_public_keys, allowed_producers=allowed_producers)
    signer_raw = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    issuer_raw = set()
    for pem in producer_public_keys.values():
        key = serialization.load_pem_public_key(pem)
        raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if raw == signer_raw:
            raise ValueError("authority and producer signing keys must be separate")
        issuer_raw.add(raw)
    if set(producer_command_public_keys) != allowed_producers:
        raise ValueError("producer command keys must match allowlist")
    command_keys = {}
    command_raw = set()
    for producer, pem in producer_command_public_keys.items():
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Ed25519 producer command key required")
        raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if raw == signer_raw or raw in issuer_raw or raw in command_raw:
            raise ValueError("signing domains must be separate")
        command_raw.add(raw)
        command_keys[producer] = key
    # Reserve writer capacity so reader polling cannot consume every slot.
    read_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synthetic-journal-read")
    write_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synthetic-journal-write")
    read_slots, write_slots = asyncio.Semaphore(1), asyncio.Semaphore(1)
    reader_requests, writer_requests = asyncio.Semaphore(7), asyncio.Semaphore(1)
    sessions = {}
    nonces = {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            read_executor.shutdown(wait=True, cancel_futures=True)
            write_executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def close_fixture_connection(request: Request, call_next):
        response = await call_next(request)
        response.headers["Connection"] = "close"
        return response

    async def io(fn, *args, writing=False, **kwargs):
        executor, slots = (write_executor, write_slots) if writing else (read_executor, read_slots)
        await slots.acquire()
        loop = asyncio.get_running_loop()
        try:
            future = executor.submit(fn, *args, **kwargs)
        except BaseException:
            slots.release()
            raise
        def finished(_):
            try:
                loop.call_soon_threadsafe(slots.release)
            except RuntimeError:
                pass
        future.add_done_callback(finished)
        return await asyncio.shield(asyncio.wrap_future(future))

    def authenticate_reader(request):
        token = extract_bearer(request.scope["headers"])
        if not hmac.compare_digest(token.encode("ascii"), reader_secret.encode("ascii")):
            raise ValueError("invalid credential")

    def remember_nonce(nonce):
        now = time.monotonic()
        for value, expiry in list(nonces.items()):
            if expiry <= now:
                del nonces[value]
        if nonce in nonces or len(nonces) >= MAX_NONCES:
            raise ValueError("nonce replay or capacity")
        nonces[nonce] = now + SESSION_TTL_SECONDS

    def clean_sessions():
        now = time.monotonic()
        for value, (_, expires) in list(sessions.items()):
            if expires <= now:
                del sessions[value]

    def signed(payload):
        value = payload.model_dump(mode="json") if isinstance(payload, Contract) else payload
        signature = signing_key.sign(_DOMAIN + canonical_json(value))
        envelope = {"frame": value, "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")}
        if len(canonical_json(envelope)) > MAX_FRAME_BYTES:
            raise JournalError("frame exceeds bound")
        return envelope

    @app.post("/v1/append")
    async def append(request: Request):
        if writer_requests.locked(): return _error(503)
        async with writer_requests:
            try:
                async with asyncio.timeout(3):
                    producer = verifier.verify(extract_bearer(request.scope["headers"]))
                    raw = await _body(request)
                    json.loads(raw, object_pairs_hook=_unique)
                    data = AppendRequest.model_validate_json(raw)
                    command_keys[producer.sub].verify(_unb64(data.signature),
                                                       deletion_command_bytes(producer.sub, data.nonce, data.control))
                    remember_nonce(data.nonce)
                    status, seq = await io(journal.append, producer.sub, data.control, writing=True)
                    issued = int(time.time())
                    receipt = {"version": 1, "kind": "append", "authority_id": journal.authority_id,
                               "issuer": issuer, "audience": "analytics-privacy-journal", "kid": kid,
                               "nonce": str(data.nonce), "control_id": str(data.control.control_id),
                               "producer": producer.sub, "payload_hash": deletion_hash(producer.sub, data.control),
                               "status": status, "seq": seq, "issued_at": issued, "expires_at": issued + FRAME_TTL_SECONDS}
                    return signed(receipt)
            except JournalConflict:
                return _error(409)
            except (JournalError, TimeoutError):
                return _error(503)
            except Exception:
                return _error(401)

    @app.post("/v1/{kind}")
    async def frame(kind: str, request: Request):
        if kind not in {"attach", "barrier", "page", "observe"}:
            return _error(404)
        if reader_requests.locked(): return _error(503)
        async with reader_requests:
            try:
                async with asyncio.timeout(3):
                    authenticate_reader(request)
                    raw = await _body(request)
                    json.loads(raw, object_pairs_hook=_unique)
                    data = FrameRequest.model_validate_json(raw)
                    clean_sessions()
                    remember_nonce(data.nonce)
                    if kind == "attach":
                        if data.session is not None or data.through is not None or len(sessions) >= MAX_SESSIONS:
                            raise ValueError("invalid attach")
                        session = uuid4()
                    else:
                        if data.session not in sessions:
                            raise ValueError("unknown session")
                        initial_after, _ = sessions[data.session]
                        if data.after < initial_after or (kind == "barrier" and data.after != initial_after):
                            raise ValueError("invalid session cursor")
                        session = data.session
                    if kind != "page" and data.through is not None:
                        raise ValueError("invalid range")
                    if kind == "page" and data.through is None:
                        raise ValueError("invalid range")
                    hwm, entries = await io(journal.snapshot, after=data.after,
                                            through=data.through if kind == "page" else data.after,
                                            limit=data.limit)
                    scanned = entries[-1].seq if entries else data.after
                    issued = int(time.time())
                    result = AuthorityFrame(version=1, authority_id=journal.authority_id, issuer=issuer,
                        audience="analytics-privacy-journal", kid=kid, kind=kind, nonce=data.nonce,
                        session=session, high_watermark=hwm, after=data.after, through=data.through,
                        scanned_through=scanned, entries=entries, issued_at=issued, expires_at=issued + FRAME_TTL_SECONDS)
                    sessions[session] = (data.after if kind == "attach" else sessions[session][0], time.monotonic() + SESSION_TTL_SECONDS)
                    return signed(result)
            except (JournalError, TimeoutError):
                return _error(503)
            except Exception:
                return _error(401)

    return app


LOCAL_ROOT = Path(__file__).resolve().parents[3] / ".local"


def _fixture_path(value: str) -> Path:
    root = LOCAL_ROOT.resolve()
    path = Path(value).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("fixture file outside local root")
    return path


def _read_limited(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("fixture file too large")
    return data


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)
    secretgen = commands.add_parser("reader-secret")
    secretgen.add_argument("--output", required=True)
    init = commands.add_parser("init")
    init.add_argument("--journal", required=True)
    init.add_argument("--authority-id", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--journal", required=True)
    serve.add_argument("--authority-id", required=True)
    serve.add_argument("--config", required=True)
    serve.add_argument("--private-fixture-network", action="store_true")
    append_cmd = commands.add_parser("append")
    append_cmd.add_argument("--client-config", required=True)
    append_cmd.add_argument("--control-file", required=True)
    append_cmd.add_argument("--producer", required=True)
    append_cmd.add_argument("--producer-token-file", required=True)
    append_cmd.add_argument("--command-key", required=True)
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            private_path, public_path = _fixture_path(args.private_key), _fixture_path(args.public_key)
            if private_path == public_path or private_path.exists() or public_path.exists():
                raise ValueError("output already exists")
            key = Ed25519PrivateKey.generate()
            _write_new(private_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            _write_new(public_path, key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
            print("synthetic key pair created")
            return
        if args.command == "reader-secret":
            _write_new(_fixture_path(args.output), base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"="))
            print("synthetic reader credential created")
            return
        if args.command == "append":
            from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
            config = AuthorityConfig.from_json(_read_limited(_fixture_path(args.client_config), 128 * 1024))
            control = DeletionFence.model_validate_json(_read_limited(_fixture_path(args.control_file), MAX_REQUEST_BYTES))
            token = _read_limited(_fixture_path(args.producer_token_file), 16 * 1024).decode("ascii").strip()
            key = serialization.load_pem_private_key(_read_limited(_fixture_path(args.command_key), 8192), password=None)
            receipt = asyncio.run(AuthorityClient(config).append(control, producer=args.producer, producer_token=token, command_key=key))
            print("synthetic journal append " + receipt.status)
            return
        journal_path = _fixture_path(args.journal)
        if args.command == "init":
            Journal.initialize(journal_path, args.authority_id)
            print("synthetic authority journal initialized")
            return
        raw = _read_limited(_fixture_path(args.config), 128 * 1024)
        cfg = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(cfg, dict) or set(cfg) != {"fixture_mode", "bind_host", "port", "issuer", "kid", "signing_key_file", "producer_issuer", "producer_public_keys", "allowed_producers", "producer_command_public_keys", "reader_secret_file"}:
            raise ValueError("invalid config")
        if cfg["fixture_mode"] is not True or type(cfg["port"]) is not int or not 1 <= cfg["port"] <= 65535:
            raise ValueError("invalid config")
        if cfg["bind_host"] not in {"127.0.0.1", "::1"} and not (args.private_fixture_network and cfg["bind_host"] == "0.0.0.0"):
            raise ValueError("invalid bind")
        secret = _read_limited(_fixture_path(cfg["reader_secret_file"]), 256).decode("ascii").strip()
        key = serialization.load_pem_private_key(_read_limited(_fixture_path(cfg["signing_key_file"]), 8192), password=None)
        if not isinstance(cfg["producer_public_keys"], dict) or not isinstance(cfg["producer_command_public_keys"], dict) or not isinstance(cfg["allowed_producers"], list):
            raise ValueError("invalid producer config")
        app = create_authority_app(Journal(journal_path, args.authority_id), issuer=cfg["issuer"], kid=cfg["kid"],
            signing_key=key, producer_issuer=cfg["producer_issuer"],
            producer_public_keys={kid: pem.encode("ascii") for kid, pem in cfg["producer_public_keys"].items()},
            allowed_producers=set(cfg["allowed_producers"]),
            producer_command_public_keys={producer: pem.encode("ascii") for producer, pem in cfg["producer_command_public_keys"].items()},
            reader_secret=secret)
        import uvicorn
        uvicorn.run(app, host=cfg["bind_host"], port=cfg["port"], http=BoundedFixtureProtocol, ws="none",
                    backlog=16, limit_concurrency=MAX_CONNECTIONS, timeout_keep_alive=2,
                    h11_max_incomplete_event_size=8192, proxy_headers=False, access_log=False, log_config=None)
    except Exception:
        print("synthetic authority failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
