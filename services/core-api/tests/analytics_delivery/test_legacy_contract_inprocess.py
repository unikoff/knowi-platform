"""Cross-service HTTP contract with synthetic verifier/store, no PG or live server."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).parents[3] / "analytics"))
from pymentor_analytics.adapters.auth.verifier import GrantVerifier
from pymentor_analytics.modules.analytics.core_transfer.api.legacy_router import router
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService
from pymentor_core.adapters.analytics_http.legacy_read import LegacyReadClient
from pymentor_core.modules.analytics_delivery.signing import Signer

SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ADMIN = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def test_real_g1_wire_against_receiver_with_synthetic_store():
    async def scenario():
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        signer = Signer(issuer="fixture", kid="grant", private_pem=private)
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow")).date()
        yesterday = today - timedelta(days=1)
        class Probe:
            async def is_ready(self): return True
        class Store:
            database = SimpleNamespace(transaction_guard=object())
            async def read_subject(self, namespace, ref, today, *, deadline, visit_through=None):
                assert namespace == "core-v1" and ref == SUBJECT
                return ([{"activity_date": yesterday.isoformat(), "visited_at": None}], [])
            async def read_admin(self, namespace, subjects, today, *, actor, deadline):
                assert namespace == "core-v1" and actor == ADMIN and subjects == (SUBJECT,)
                return [{"subject_ref": str(SUBJECT), "activity_date": yesterday.isoformat()}]
        app = FastAPI()
        app.include_router(router)
        app.state.trust_manifest_validated = True
        app.state.grant_verifier = GrantVerifier(issuer="fixture", public_keys={"grant": public})
        app.state.core_transfer_probe = Probe()
        app.state.settings = SimpleNamespace(db_timeout_seconds=1)
        app.state.legacy_service = LegacyService(Store(), "core-v1", {
            "expires_at": int(time.time()) + 60, "subjects": (SUBJECT,), "students": frozenset({str(SUBJECT)})})
        client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.ASGITransport(app=app))
        user = signer.grant(subject=str(SUBJECT), role="user", scope="report:read", courses=["backend"], subjects=[], global_read=False)
        result = await client.read("calendar", user, year=yesterday.year, month=yesterday.month)
        assert result == {"coverage": "unknown", "data": {"days": [yesterday.isoformat()],
                          "visits": [{"activity_date": yesterday.isoformat(), "visited_at": None}]}}
        summary = await client.read("summary", user)
        assert summary["coverage"] == "unknown" and summary["data"]["current_streak"] == 1
        admin = signer.grant(subject=str(ADMIN), role="admin", scope="report:read", courses=[], subjects=[], global_read=True)
        result = await client.read("admin", admin)
        assert result["coverage"] == "unknown" and result["data"]["dashboard_activity"]["active_students_7d"] == 1
    asyncio.run(scenario())


def test_profile_real_core_signer_and_query_asgi_wire():
    async def scenario():
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        signer = Signer(issuer="fixture", kid="grant", private_pem=private)
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow")).date()
        class Probe:
            async def is_ready(self): return True
        class Store:
            database = SimpleNamespace(transaction_guard=object())
            browser_run_id = "synthetic"
            async def read_subject(self, namespace, ref, current, **kwargs):
                assert namespace == "core-v1" and ref == SUBJECT and kwargs["actor"] == ADMIN
                assert kwargs["require_coverage"] and kwargs["include_all_visits"]
                return ([{"activity_date": today.isoformat(), "visited_at": None}], [],
                        {"coverage_through_revision": 3, "source_snapshot_at": "2026-01-01T00:00:00+00:00"},
                        "2026-01-01T00:00:01+00:00")
        app = FastAPI()
        app.include_router(router)
        app.state.trust_manifest_validated = True
        app.state.grant_verifier = GrantVerifier(issuer="fixture", public_keys={"grant": public})
        app.state.core_transfer_probe = Probe()
        app.state.settings = SimpleNamespace(db_timeout_seconds=1)
        app.state.legacy_service = LegacyService(Store(), "core-v1", None)
        client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.ASGITransport(app=app))
        grant = signer.grant(subject=str(ADMIN), role="admin", scope="report:read", courses=[], subjects=[], global_read=True)
        result = await client.read("profile", grant, year=today.year, month=today.month, subject_ref=str(SUBJECT))
        assert result["coverage"] == "complete" and result["coverage_through_revision"] == 3
        assert result["browser_quality"] == "unknown" and result["data"]["activity_days"] == [today.isoformat()]
    asyncio.run(scenario())
