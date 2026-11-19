import base64
import ast
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_core.modules.analytics_delivery import local_collection as local
from pymentor_core.modules.analytics_delivery import local_source_key

INSTANCE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NONCE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
KEY = bytes(range(32))


def config():
    return local.LocalCollection(INSTANCE, "core_db", "postgresql+psycopg://user:password@127.0.0.1:57382/core_db", KEY)


def test_source_authentication_and_signed_fresh_response(monkeypatch):
    app = FastAPI()
    app.state.analytics_local_collection = config()
    app.include_router(local.router, prefix="/api")
    calls = []
    monkeypatch.setattr(local, "read_snapshot", lambda cfg: (calls.append(cfg) or (
        "2026-11-17T10:00:00.000000Z", [{"subject_ref": INSTANCE, "deleted_at": "2026-11-17T09:59:00.000000Z"}])))
    body = {"version": 1, "instance_id": INSTANCE, "nonce": NONCE}
    mac = local.signature(KEY, local.REQUEST_DOMAIN, body)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        url = f"/api/internal/analytics/deletions?nonce={NONCE}&instance_id={INSTANCE}"
        response = client.get(url, headers={"X-Analytics-Request-MAC": mac})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        result = response.json()
        result_mac = result.pop("mac")
        assert result == {**body, "as_of": "2026-11-17T10:00:00.000000Z", "events": [
            {"subject_ref": INSTANCE, "deleted_at": "2026-11-17T09:59:00.000000Z"}]}
        assert result_mac == local.signature(KEY, local.RESPONSE_DOMAIN, result)
        for bad_url, bad_headers in ((url, {}), (url, {"X-Analytics-Request-MAC": "0" * 64}),
            (url + "&nonce=" + NONCE, {"X-Analytics-Request-MAC": mac}),
            (url.replace(INSTANCE, NONCE), {"X-Analytics-Request-MAC": mac})):
            rejected = client.get(bad_url, headers=bad_headers)
            assert rejected.status_code == 401 and rejected.headers["cache-control"] == "no-store"
        assert len(calls) == 1
        monkeypatch.setattr(local, "read_snapshot", lambda cfg: (_ for _ in ()).throw(ValueError("source unavailable")))
        assert client.get(url, headers={"X-Analytics-Request-MAC": mac}).status_code == 503


def test_remote_source_rejected(monkeypatch):
    app = FastAPI()
    app.state.analytics_local_collection = config()
    app.include_router(local.router, prefix="/api")
    monkeypatch.setattr(local, "read_snapshot", lambda cfg: pytest.fail("remote must not read"))
    body = {"version": 1, "instance_id": INSTANCE, "nonce": NONCE}
    with TestClient(app, client=("192.0.2.5", 12345)) as client:
        response = client.get(f"/api/internal/analytics/deletions?nonce={NONCE}&instance_id={INSTANCE}",
            headers={"X-Analytics-Request-MAC": local.signature(KEY, local.REQUEST_DOMAIN, body)})
        assert response.status_code == 401


def test_local_config_is_explicit_and_bound(monkeypatch):
    monkeypatch.setattr(local_source_key, "read_source_key", lambda *_: base64.urlsafe_b64encode(KEY).rstrip(b"="))
    env = {"PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "1", "PYMENTOR_ANALYTICS_GRANTS_ENABLED": "1",
           "PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED": "1", "PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE": "core_db",
           "PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID": INSTANCE, "PYMENTOR_ANALYTICS_LOCAL_SOURCE_KEY_FILE": "private",
           "PYMENTOR_ANALYTICS_LOCAL_DATASET_ID": NONCE, "PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE": "core",
           "PYMENTOR_ANALYTICS_LOCAL_CATALOG_URL": "http://127.0.0.1:18042",
           "PYMENTOR_ANALYTICS_LOCAL_CATALOG_PRODUCER_KEY_SHA256": "a" * 64,
           "PYMENTOR_ANALYTICS_PRODUCER_ID": "core", "PYMENTOR_ANALYTICS_PRODUCER_ISSUER": "issuer",
           "PYMENTOR_ANALYTICS_PRODUCER_KID": "kid"}
    url = config().database_url
    assert local.configure({}, url) is None
    assert local.configure(env, url).key == KEY
    for update in ({"PYMENTOR_ANALYTICS_LEGACY_READ_MODE": "analytics"},
                   {"PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "1"},
                   {"PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE": "other"}):
        with pytest.raises(ValueError):
            local.configure({**env, **update}, url)
    transfer = {**env, "PYMENTOR_ANALYTICS_LOCAL_TRANSFER_ENABLED": "1",
                "PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "1", "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED": "1",
                "PYMENTOR_ANALYTICS_QUERY_URL": "http://127.0.0.1:18043"}
    assert local.configure(transfer, url) == local.configure(env, url)
    switched = {**transfer, "PYMENTOR_ANALYTICS_LEGACY_READ_MODE": "analytics",
                "PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED": "1"}
    assert local.configure(switched, url).key == KEY
    for update in ({"PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED": "0"},
                   {"PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE": "outbox_only"},
                   {"PYMENTOR_ANALYTICS_QUERY_URL": "https://example.org"}):
        with pytest.raises(ValueError):
            local.configure({**switched, **update}, url)
    with pytest.raises(ValueError):
        local.configure(env, url.replace("127.0.0.1", "192.0.2.1"))
    assert local._stamp(datetime(2026, 11, 17, tzinfo=timezone.utc)) == "2026-11-17T00:00:00.000000Z"
    with pytest.raises(ValueError):
        local._stamp(datetime(2026, 11, 17))


def test_app_constructs_local_mode_only_with_independent_grant_key(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from pymentor_core.app.factory import create_app
    key = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    original = local.Path.read_bytes
    def read(path):
        if str(path) == "grant-test": return key
        return original(path)
    monkeypatch.setattr(local.Path, "read_bytes", read)
    monkeypatch.setattr(local_source_key, "read_source_key", lambda *_: base64.urlsafe_b64encode(KEY).rstrip(b"="))
    values = {"DATABASE_URL": "postgresql+psycopg://synthetic@127.0.0.1:1/isolated",
              "REDIS_URL": "redis://127.0.0.1:1/0", "KNOWI_REDIS_PREFIX": "analytics-smoke",
              "KNOWI_LOGIN_RECOVERY_KEY": "a" * 64, "PYMENTOR_ANALYTICS_GRANTS_ENABLED": "1",
              "PYMENTOR_ANALYTICS_GRANT_KEY_FILE": "grant-test", "PYMENTOR_ANALYTICS_GRANT_ISSUER": "test",
              "PYMENTOR_ANALYTICS_GRANT_KID": "test", "PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED": "1",
              "PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "1", "PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE": "isolated",
              "PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID": INSTANCE, "PYMENTOR_ANALYTICS_LOCAL_SOURCE_KEY_FILE": "local-source-test",
              "PYMENTOR_ANALYTICS_LOCAL_DATASET_ID": NONCE, "PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE": "core",
              "PYMENTOR_ANALYTICS_LOCAL_CATALOG_URL": "http://127.0.0.1:18042",
              "PYMENTOR_ANALYTICS_LOCAL_CATALOG_PRODUCER_KEY_SHA256": "a" * 64,
              "PYMENTOR_ANALYTICS_PRODUCER_ID": "core", "PYMENTOR_ANALYTICS_PRODUCER_ISSUER": "issuer",
              "PYMENTOR_ANALYTICS_PRODUCER_KID": "kid", "PYMENTOR_ANALYTICS_LEGACY_READ_MODE": "off",
              "PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE": "mirror", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "0",
              "PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED": "0"}
    for name, value in values.items(): monkeypatch.setenv(name, value)
    app = create_app()
    assert app.state.analytics_local_collection.instance_id == INSTANCE
    response = TestClient(app, client=("127.0.0.1", 12345)).get("/api/internal/analytics/deletions")
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    key = Ed25519PrivateKey.from_private_bytes(KEY).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    with pytest.raises(ValueError, match="source key must be independent"):
        create_app()
    monkeypatch.setattr(local_source_key, "read_source_key", lambda *_: (_ for _ in ()).throw(ValueError("ACL")))
    with pytest.raises(ValueError, match="Invalid local Analytics source configuration"):
        create_app()


def test_source_rejects_disabled_or_modified_deletion_trigger():
    migration = local.Path(__file__).resolve().parents[2] / "migrations/versions/c426_deletion_outbox.py"
    bodies = {}
    for node in ast.walk(ast.parse(migration.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            sql = node.args[0].value
            for name in ("pymentor_capture_account_deletion(p_user_id integer)", "pymentor_capture_account_deletion_trigger()"):
                if "CREATE FUNCTION " + name in sql:
                    bodies[name] = sql.split(" AS $$", 1)[1].split("$$ LANGUAGE", 1)[0]
    trigger = bodies["pymentor_capture_account_deletion_trigger()"]
    capture = bodies["pymentor_capture_account_deletion(p_user_id integer)"]
    class Result:
        def __init__(self, value): self.value = value
        def all(self): return self.value
        def scalar_one(self): return self.value
    class Connection:
        enabled, kind, body = "O", 11, trigger
        def execute(self, sql):
            query = str(sql)
            if "pg_attribute a JOIN" in query:
                return Result([("lifecycle_id", "character varying(36)", True), ("user_id", "integer", True),
                               ("deleted_at", "timestamp with time zone", True)])
            if "array_agg" in query: return Result(["lifecycle_id"])
            return Result([(self.enabled, self.kind, self.body, capture)])
    conn = Connection()
    assert local._schema_ok(conn)
    conn.enabled = "D"
    assert not local._schema_ok(conn)
    conn.enabled, conn.body = "O", trigger + " RAISE EXCEPTION 'modified';"
    assert not local._schema_ok(conn)


@pytest.mark.parametrize("replica, count", [(True, 0), (False, 1025)])
def test_source_rejects_replica_and_overflow(monkeypatch, replica, count):
    class Result:
        def __init__(self, value): self.value = value
        def one(self): return self.value
        def all(self): return self.value
        def scalar_one(self): return self.value
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execution_options(self, **kwargs): return self
        def begin(self): return self
        def execute(self, sql):
            query = str(sql)
            if "current_database" in query: return Result(("core_db", replica))
            if "clock_timestamp" in query: return Result(datetime.now(timezone.utc))
            if "SELECT lifecycle_id" in query: return Result([(INSTANCE, datetime.now(timezone.utc))] * count)
            return Result(None)
    class Engine:
        def connect(self): return Connection()
        def dispose(self): pass
    monkeypatch.setattr(local, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(local, "_schema_ok", lambda conn: True)
    with pytest.raises(ValueError):
        local.read_snapshot(config())
