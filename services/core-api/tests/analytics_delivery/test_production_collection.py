import base64

import pytest

from pymentor_core.app.analytics_publisher import authority_tls
from pymentor_core.modules.analytics_delivery.production_collection import configure
from pymentor_core.adapters.analytics_http.http import client_tls


def source_env():
    return {
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_ENABLED": "1",
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_DATABASE_URL":
            "postgresql+psycopg://reader:password@core-db/core?sslmode=verify-full&sslrootcert=%2Fca.pem",
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_HOST": "core-db",
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_DATABASE": "core",
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_USER": "reader",
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_INSTANCE_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_KEY_FILE": "source.key",
    }


def test_private_source_pins_identity_and_requires_verified_database_tls(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "read_bytes", lambda self: base64.urlsafe_b64encode(b"a" * 32).rstrip(b"="))
    env = source_env()
    source = configure(env)
    assert (source.database, source.expected_user, source.key) == ("core", "reader", b"a" * 32)
    for name in ("PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_HOST", "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_DATABASE",
                 "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_USER"):
        with pytest.raises(ValueError):
            configure({**env, name: "wrong"})
    with pytest.raises(ValueError):
        configure({**env, "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_DATABASE_URL": env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_DATABASE_URL"].replace("verify-full", "require")})
    with pytest.raises(ValueError):
        configure({**env, "PYMENTOR_ANALYTICS_PRIVATE_SOURCE_ENABLED": "0"})
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"invalid")
    with pytest.raises(ValueError):
        configure(env)


def test_authority_tls_requires_complete_mtls_for_non_loopback(monkeypatch):
    import ssl

    env = {"PYMENTOR_ANALYTICS_AUTHORITY_URL": "https://authority.internal"}
    with pytest.raises(ValueError):
        authority_tls(env)
    calls = []
    class Context:
        def load_cert_chain(self, **kwargs):
            calls.append(kwargs)
    context = Context()
    monkeypatch.setattr(ssl, "create_default_context", lambda **kwargs: calls.append(kwargs) or context)
    env.update(PYMENTOR_ANALYTICS_AUTHORITY_CA_FILE="ca", PYMENTOR_ANALYTICS_AUTHORITY_CLIENT_CERT_FILE="cert",
               PYMENTOR_ANALYTICS_AUTHORITY_CLIENT_KEY_FILE="key")
    assert authority_tls(env) is context
    assert calls == [{"cafile": "ca"}, {"certfile": "cert", "keyfile": "key"}]
    with pytest.raises(ValueError):
        authority_tls({**env, "PYMENTOR_ANALYTICS_AUTHORITY_URL": "http://authority.internal"})
    assert authority_tls({"PYMENTOR_ANALYTICS_AUTHORITY_URL": "http://127.0.0.1"}) is True


@pytest.mark.parametrize("prefix", ["PYMENTOR_ANALYTICS_QUERY", "PYMENTOR_ANALYTICS_TRANSFER"])
def test_remote_clients_require_complete_tls(monkeypatch, prefix):
    import ssl
    class Context:
        def load_cert_chain(self, **kwargs):
            assert kwargs == {"certfile": "cert", "keyfile": "key"}
    context = Context()
    monkeypatch.setattr(ssl, "create_default_context", lambda **kwargs: context)
    with pytest.raises(ValueError):
        client_tls({}, prefix, "https://analytics.internal")
    env = {prefix + "_CA_FILE": "ca", prefix + "_CLIENT_CERT_FILE": "cert", prefix + "_CLIENT_KEY_FILE": "key"}
    assert client_tls(env, prefix, "https://analytics.internal") is context
    with pytest.raises(ValueError):
        client_tls({**env, prefix + "_CLIENT_KEY_FILE": ""}, prefix, "https://analytics.internal")
    with pytest.raises(ValueError):
        client_tls(env, prefix, "http://analytics.internal")
