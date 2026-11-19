import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from pymentor_analytics.adapters.auth.config import load_verifiers, validate_authority_key_separation
from pymentor_analytics.app.config import Settings, load_settings
from pymentor_analytics.app.factory import create_app


URL = "postgresql://synthetic@127.0.0.1:57392/analytics_test"


def public():
    return Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def manifest(grant, producer):
    return SecretStr(json.dumps({"grant": {"issuer": "synthetic-grants", "keys": {"g1": grant}},
                               "producer": {"issuer": "synthetic-control", "keys": {"p1": producer}, "allowlist": ["synthetic"]}}))


def test_authority_setting_is_shared_and_hidden_for_every_process_role():
    secret = '{"reader_secret":"do-not-show"}'
    for role in ("collector", "processor", "query", "privacy"):
        env = {f"ANALYTICS_{role.upper()}_DATABASE_URL": URL, "ANALYTICS_PRIVACY_AUTHORITY_JSON": secret}
        settings = load_settings(role, env)
        assert settings.privacy_authority_json.get_secret_value() == secret
        assert "do-not-show" not in repr(settings)
        env[f"ANALYTICS_{role.upper()}_PRIVACY_AUTHORITY_JSON"] = secret
        with pytest.raises(ValueError, match="shared"):
            load_settings(role, env)


def test_response_signer_cannot_reuse_grant_or_producer_jwt_key():
    grant, producer, response = public(), public(), public()
    settings = Settings(database_url=URL, trust_manifest_json=manifest(grant, producer))
    validate_authority_key_separation(settings, {"a1": response})
    for reused in (grant, producer):
        with pytest.raises(ValueError, match="invalid authority configuration"):
            validate_authority_key_separation(settings, {"different-kid": reused})
    assert load_verifiers(settings, role="privacy") == (None, None)


def test_configured_authority_cannot_be_bypassed_by_readiness_injection():
    class Ready:
        async def is_ready(self): return True
    config = SecretStr(json.dumps({"url": "http://127.0.0.1:57420", "authority_id": "synthetic",
                                  "issuer": "synthetic-authority", "public_keys": {"a1": public()},
                                  "fixture_mode": True,
                                  "reader_secret": base64.urlsafe_b64encode(b"r"*32).rstrip(b"=").decode("ascii")}))
    with pytest.raises(ValueError, match="cannot use an injected readiness"):
        with TestClient(create_app("query", Settings(database_url=URL, privacy_authority_json=config), Ready())):
            raise AssertionError("injected readiness bypassed authority")
