import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import UUID

import pytest
import httpx

jwt = pytest.importorskip("jwt")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pymentor_core.modules.analytics_delivery.signing import Signer
from pymentor_core.modules.analytics_delivery.contracts import deletion
from pymentor_core.adapters.analytics_http.privacy import AuthorityClient, _FRAME_DOMAIN, _COMMAND_DOMAIN, _canonical, _b64
from pymentor_core.adapters.analytics_http.transport import TransferHttp
from pymentor_core.modules.analytics_delivery.relay import Receipt
from hashlib import sha256

SUBJECT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class Stream(httpx.AsyncByteStream):
    def __init__(self, data): self.data = data
    async def __aiter__(self): yield self.data


def pem_private(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def pem_public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def test_ed25519_grant_and_producer_wire_contract():
    key = Ed25519PrivateKey.generate()
    signer = Signer(issuer="synthetic-issuer", kid="key-1", private_pem=pem_private(key))
    token = signer.grant(subject=SUBJECT, role="user", scope="collect", courses=["backend"],
                         subjects=[], global_read=False)
    claims = jwt.decode(token, key.public_key(), algorithms=["EdDSA"], audience="analytics", issuer="synthetic-issuer")
    assert jwt.get_unverified_header(token) == {"alg": "EdDSA", "typ": "JWT", "kid": "key-1"}
    assert claims["exp"] - claims["iat"] == 300 and claims["sub"] == SUBJECT
    producer_key = Ed25519PrivateKey.generate()
    producer_signer = Signer(issuer="synthetic-issuer", kid="producer-1", private_pem=pem_private(producer_key), purpose="producer")
    producer = jwt.decode(producer_signer.producer("core"), producer_key.public_key(), algorithms=["EdDSA"],
                          audience="analytics-control", issuer="synthetic-issuer")
    assert producer["exp"] - producer["iat"] == 60 and producer["scope"] == "control:write"
    with pytest.raises(ValueError):
        signer.producer("core")
    with pytest.raises(ValueError):
        signer.grant(subject=SUBJECT, role="user", scope="report:read", courses=["backend"], subjects=[SUBJECT], global_read=False)


def test_authority_append_verifies_signed_receipt_and_stable_control():
    command_key, authority_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    class Opener:
        def __init__(self): self.controls = []
        def handle(self, request):
            body = json.loads(request.content)
            control = body["control"]
            self.controls.append(control)
            import base64
            signature = body["signature"]
            command_key.public_key().verify(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
                _COMMAND_DOMAIN + _canonical({"producer": "core", "nonce": body["nonce"], "control": control}))
            assert request.headers["Authorization"] == "Bearer synthetic"
            now = int(time.time())
            frame = {"version": 1, "kind": "append", "authority_id": "authority", "issuer": "issuer",
                     "audience": "analytics-privacy-journal", "kid": "key", "nonce": body["nonce"],
                     "control_id": control["control_id"], "producer": "core", "status": "accepted", "seq": 1,
                     "payload_hash": sha256(_canonical({"producer": "core", "control": control})).hexdigest(),
                     "issued_at": now, "expires_at": now + 3}
            wire = _canonical({"frame": frame, "signature": _b64(authority_key.sign(_FRAME_DOMAIN + _canonical(frame)))})
            return httpx.Response(200, stream=Stream(wire))
    opener = Opener()
    client = AuthorityClient(endpoint="http://127.0.0.1:1", authority_id="authority", issuer="issuer",
        public_keys={"key": pem_public(authority_key)}, producer="core", producer_token=lambda: "synthetic",
        command_key=command_key, transport=httpx.MockTransport(opener.handle))
    event = deletion(UUID(SUBJECT), revision=3)
    created = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    assert asyncio.run(client.append(event, created, timeout_seconds=2)) == 1
    assert asyncio.run(client.append(event, created, timeout_seconds=2)) == 1
    assert opener.controls[0] == opener.controls[1]
    assert opener.controls[0]["occurred_at"] == "2026-11-17T12:00:00Z"


def test_transfer_http_is_bounded_and_sends_only_authorized_wire():
    from pymentor_core.modules.analytics_delivery.contracts import activity, fingerprint
    from datetime import date
    event = activity(UUID(SUBJECT), date(2026, 11, 17), None, revision=0)
    class Opener:
        def __init__(self): self.requests = []
        def handle(self, request):
            self.requests.append(request)
            return httpx.Response(200, stream=Stream(b'{"status":"accepted"}'))
    opener = Opener()
    transport = TransferHttp("http://localhost:1", lambda: "synthetic-token", transport=httpx.MockTransport(opener.handle))
    assert asyncio.run(transport.send(event, 2)) is Receipt.ACCEPTED
    request = opener.requests[0]
    assert request.headers["Authorization"] == "Bearer synthetic-token"
    assert json.loads(request.content) == {"event": event, "fingerprint": fingerprint(event)}
    assert asyncio.run(transport.lookup(event["event_id"], fingerprint(event), 1)) is Receipt.ACCEPTED
    assert "/receipts/" in str(opener.requests[1].url)
    oversized = TransferHttp("http://localhost:1", lambda: "synthetic-token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream(b"x" * 4097))))
    assert asyncio.run(oversized.send(event, 2)) is Receipt.UNKNOWN
    with pytest.raises(ValueError):
        TransferHttp("http://example.test", lambda: "synthetic-token")
