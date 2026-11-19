"""Real loopback slow peers; no external services, database, or background threads."""
import asyncio
import json
import threading
import time
from datetime import date, datetime, timezone
from hashlib import sha256
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pymentor_core.adapters.analytics_http.privacy import AuthorityClient, _FRAME_DOMAIN, _canonical, _b64
from pymentor_core.adapters.analytics_http.transport import TransferHttp
from pymentor_core.adapters.sql.analytics_delivery import metadata, outbox, enqueue
from pymentor_core.adapters.sql.analytics_delivery_ops import worker
from pymentor_core.modules.analytics_delivery.contracts import activity, deletion

SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest.mark.parametrize("kind", ["transfer", "authority"])
@pytest.mark.parametrize("delay", ["headers", "body"])
def test_slow_peer_total_deadline_retry_and_socket_cleanup(synthetic_engine, kind, delay, monkeypatch):
    async def scenario():
        before_threads = {thread.ident for thread in threading.enumerate()}
        disconnected = asyncio.Event()
        tasks = set()
        bodies = []
        authority_key, command_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()

        async def handle(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.decode("ascii").split("\r\n")
                method, path, _ = lines[0].split()
                length = next((int(line.split(":", 1)[1]) for line in lines[1:] if line.lower().startswith("content-length:")), 0)
                data = json.loads(await reader.readexactly(length)) if length else None
                if method == "GET":
                    wire = b'{"status":"absent"}'
                else:
                    bodies.append(data)
                    if len(bodies) == 1:
                        watcher = asyncio.create_task(reader.read())
                        try:
                            if delay == "body":
                                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 10000\r\n\r\n{")
                                await writer.drain()
                                while not watcher.done():
                                    writer.write(b" ")
                                    await writer.drain()
                                    await asyncio.sleep(0.02)
                            await watcher
                        finally:
                            if not watcher.done():
                                watcher.cancel()
                            await asyncio.gather(watcher, return_exceptions=True)
                            disconnected.set()
                        return
                    if kind == "transfer":
                        wire = b'{"status":"accepted"}'
                    else:
                        now = int(time.time())
                        control = data["control"]
                        frame = {"version": 1, "kind": "append", "authority_id": "authority", "issuer": "issuer",
                                 "audience": "analytics-privacy-journal", "kid": "key", "nonce": data["nonce"],
                                 "control_id": control["control_id"], "producer": "core", "status": "duplicate", "seq": 1,
                                 "payload_hash": sha256(_canonical({"producer": "core", "control": control})).hexdigest(),
                                 "issued_at": now, "expires_at": now + 3}
                        wire = _canonical({"frame": frame, "signature": _b64(authority_key.sign(_FRAME_DOMAIN + _canonical(frame)))})
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                             + str(len(wire)).encode() + b"\r\n\r\n" + wire)
                await writer.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                disconnected.set()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                tasks.discard(task)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        engine = None
        try:
            if kind == "transfer":
                engine = synthetic_engine
                metadata.create_all(engine)
                sessions = sessionmaker(engine)
                event = activity(SUBJECT, date(2026, 11, 17), None, revision=0)
                with sessions() as db:
                    enqueue(db, event, datetime.now(timezone.utc))
                    db.commit()
                monkeypatch.setattr(worker, "retry_delay", lambda *args, **kwargs: 0)
                transport = TransferHttp(endpoint, lambda: "synthetic")
                started = time.monotonic()
                assert await worker.run_once(sessions, transport, deadline_seconds=0.3) == 1
                elapsed = time.monotonic() - started
                print(f"{kind}/{delay}: timeout returned in {elapsed:.3f}s (budget 0.300s)")
                assert elapsed < 0.8
                with sessions() as db:
                    assert db.execute(select(outbox.c.status, outbox.c.uncertain, outbox.c.claim_token, outbox.c.lease_until)).one() == ("pending", True, None, None)
                await asyncio.wait_for(disconnected.wait(), 1)
                assert await worker.run_once(sessions, transport, deadline_seconds=2) == 1
                with sessions() as db:
                    assert db.execute(select(outbox.c.status)).scalar_one() == "delivered"
                assert bodies[0]["event"]["event_id"] == bodies[1]["event"]["event_id"] == event["event_id"]
            else:
                public = authority_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
                client = AuthorityClient(endpoint=endpoint, authority_id="authority", issuer="issuer", public_keys={"key": public},
                    producer="core", producer_token=lambda: "synthetic", command_key=command_key)
                event = deletion(SUBJECT, revision=3)
                created = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
                started = time.monotonic()
                assert await client.append(event, created, timeout_seconds=0.3) is None
                elapsed = time.monotonic() - started
                print(f"{kind}/{delay}: timeout returned in {elapsed:.3f}s (budget 0.300s)")
                assert elapsed < 0.8
                await asyncio.wait_for(disconnected.wait(), 1)
                assert await client.append(event, created, timeout_seconds=2) == 1
                assert bodies[0]["control"] == bodies[1]["control"]
                assert bodies[0]["nonce"] != bodies[1]["nonce"]
        finally:
            server.close()
            await server.wait_closed()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*list(tasks), return_exceptions=True), 2)
            if engine is not None:
                engine.dispose()
        assert not tasks
        assert {thread.ident for thread in threading.enumerate()} <= before_threads
    asyncio.run(scenario())
