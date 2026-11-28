"""Local-only authenticated, uncached Core deletion snapshot for Analytics."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import ipaddress
import json
from pathlib import Path
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from starlette.concurrency import run_in_threadpool

REQUEST_DOMAIN = b"pymentor:analytics:core-deletions:request:v1\0"
RESPONSE_DOMAIN = b"pymentor:analytics:core-deletions:response:v1\0"
_BODY_HASH = "26d2d2c45e3b7f01b31817fb0445503d826563dfe3ae065d3001e8a7df4ad5d8"
_TRIGGER_HASH = "c1c76075cdd0ccecf033ace6b4671fc5f74b907b8132588abb96addf389d9dad"
router = APIRouter()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def signature(key, domain, value):
    return hmac.new(key, domain + canonical(value), sha256).hexdigest()


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("invalid UUID")
    return value


@dataclass(frozen=True)
class LocalCollection:
    instance_id: str
    database: str
    database_url: str = field(repr=False)
    key: bytes = field(repr=False)
    catalog_binding: object = field(default=None, repr=False)
    expected_user: str | None = None
    engine: Engine = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        # Only connections are reused. Every signed response reads a new transaction.
        engine = create_engine(
            self.database_url, pool_size=2, max_overflow=0, pool_timeout=1,
            pool_pre_ping=True, isolation_level="READ COMMITTED", hide_parameters=True,
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000 -c lock_timeout=1000"},
        )
        object.__setattr__(self, "engine", engine)


def configure(env, database_url):
    flag = env.get("PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED", "0")
    transfer = env.get("PYMENTOR_ANALYTICS_LOCAL_TRANSFER_ENABLED", "0")
    if flag not in {"0", "1"} or transfer not in {"0", "1"} or (flag == "0" and transfer == "1"):
        raise ValueError("Invalid local Analytics collection setting")
    if flag == "0":
        return None
    read_mode = env.get("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "off")
    browser_required = env.get("PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED", "0")
    if (env.get("PYMENTOR_ANALYTICS_GRANTS_ENABLED") != "1"
            or env.get("PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED") != "1"
            or env.get("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror") != "mirror"
            or env.get("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "0") != transfer
            or env.get("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "0") != transfer
            or read_mode not in ({"off", "shadow", "analytics"} if transfer == "1" else {"off"})
            or browser_required not in ({"0", "1"} if read_mode == "analytics" else {"0"})
            or (transfer == "1" and env.get("PYMENTOR_ANALYTICS_QUERY_URL") != "http://127.0.0.1:18043")):
        raise ValueError("Invalid local Analytics collection configuration")
    try:
        parsed = make_url(database_url)
        if (parsed.drivername != "postgresql+psycopg" or parsed.host not in {"127.0.0.1", "::1"}
                or parsed.database != env["PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE"] or parsed.query):
            raise ValueError
        instance = _uuid(env["PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID"])
        from pymentor_core.modules.analytics_delivery.local_source_key import read_source_key
        encoded = read_source_key(env["PYMENTOR_ANALYTICS_LOCAL_SOURCE_KEY_FILE"], env["PYMENTOR_ANALYTICS_LOCAL_DATASET_ID"])
        if not re.fullmatch(rb"[A-Za-z0-9_-]{43}", encoded):
            raise ValueError
        key = base64.urlsafe_b64decode(encoded + b"=")
        if len(key) != 32 or base64.urlsafe_b64encode(key).rstrip(b"=") != encoded:
            raise ValueError
        from pymentor_core.app.analytics_catalog import binding_from_env
        return LocalCollection(instance, parsed.database, database_url, key, binding_from_env(env, instance))
    except Exception:
        raise ValueError("Invalid local Analytics source configuration") from None


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid source timestamp")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_SOURCE_IDENTITY = text("SELECT current_database(),pg_is_in_recovery(),current_user")
_SOURCE_PREFLIGHT = text("""/* Core deletion snapshot preflight */
    WITH event_table AS (
        SELECT c.oid,c.relowner FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='account_deletion_events' AND c.relkind='r'
    )
    SELECT current_database(),pg_is_in_recovery(),current_user,
        (SELECT jsonb_agg(jsonb_build_array(a.attname::text,format_type(a.atttypid,a.atttypmod),a.attnotnull)
                          ORDER BY a.attnum)
            FROM event_table c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
            WHERE a.attnum>0 AND NOT a.attisdropped),
        (SELECT array_agg(a.attname::text ORDER BY u.ord)
            FROM event_table c JOIN pg_catalog.pg_constraint k ON k.conrelid=c.oid
            CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum,ord)
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=u.attnum
            WHERE k.contype='p'),
        (SELECT jsonb_agg(jsonb_build_array(t.tgenabled::text,t.tgtype,p.prosrc,q.prosrc))
            FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
            JOIN pg_catalog.pg_proc q ON q.proname='pymentor_capture_account_deletion'
                 AND q.pronamespace=pn.oid AND q.pronargs=1 AND q.proargtypes='23'::oidvector
            WHERE n.nspname='public' AND c.relname='users' AND t.tgname='pymentor_account_deletion_event_v1'
              AND NOT t.tgisinternal AND t.tgqual IS NULL AND t.tgnargs=0
              AND pn.nspname='public' AND p.proname='pymentor_capture_account_deletion_trigger'
              AND p.pronargs=0),
        CASE WHEN :private_reader THEN (
            SELECT NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreaterole AND NOT r.rolcreatedb
                AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid)
                AND NOT pg_has_role(current_user, c.relowner, 'MEMBER')
                AND has_column_privilege('public.account_deletion_events', 'lifecycle_id', 'SELECT')
                AND has_column_privilege('public.account_deletion_events', 'deleted_at', 'SELECT')
                AND NOT has_column_privilege('public.account_deletion_events', 'user_id', 'SELECT')
                AND NOT has_table_privilege('public.account_deletion_events', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                AND NOT has_any_column_privilege('public.account_deletion_events', 'INSERT,UPDATE,REFERENCES')
            FROM event_table c CROSS JOIN pg_catalog.pg_roles r
            WHERE r.rolname=current_user
        ) ELSE TRUE END,
        clock_timestamp()
""")


def _schema_ok(columns, pk, triggers):
    if columns != [["lifecycle_id", "character varying(36)", True],
                   ["user_id", "integer", True], ["deleted_at", "timestamp with time zone", True]]:
        return False
    if pk != ["lifecycle_id"] or not triggers or len(triggers) != 1:
        return False
    enabled, kind, trigger_body, capture_body = triggers[0]
    digest = lambda value: sha256(" ".join(value.split()).encode("utf-8")).hexdigest()
    return enabled in {"O", "A"} and kind == 11 and digest(trigger_body) == _TRIGGER_HASH and digest(capture_body) == _BODY_HASH


def _source_identity_ok(config, identity):
    database, recovery, user = identity
    return database == config.database and not recovery and (config.expected_user is None or user == config.expected_user)


def read_snapshot(config):
    with config.engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            database, recovery, user, columns, pk, triggers, reader_ok, timestamp = connection.execute(
                _SOURCE_PREFLIGHT, {"private_reader": config.expected_user is not None},
            ).one()
            if (not _source_identity_ok(config, (database, recovery, user))
                    or reader_ok is not True or not _schema_ok(columns, pk, triggers)):
                raise ValueError("source unavailable")
            # Preflight finishes before the separate READ COMMITTED event
            # statement takes its snapshot, so this clock cannot overstate it.
            as_of = _stamp(timestamp)
            rows = connection.execute(text("""SELECT lifecycle_id,deleted_at FROM public.account_deletion_events
                ORDER BY lifecycle_id COLLATE "C" LIMIT 1025""")).all()
            if len(rows) > 1024:
                raise ValueError("source overflow")
            events = [{"subject_ref": _uuid(row[0]), "deleted_at": _stamp(row[1])} for row in rows]
            if not _source_identity_ok(config, connection.execute(_SOURCE_IDENTITY).one()):
                raise ValueError("source changed")
            return as_of, events


@router.get("/internal/analytics/deletions", include_in_schema=False)
async def deletions(request: Request):
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    config = getattr(request.app.state, "analytics_local_collection", None)
    params = request.query_params
    # Uvicorn's access logger reads this ASGI field when sending the response.
    request.scope["query_string"] = b""
    try:
        if config is None or not request.client or not ipaddress.ip_address(request.client.host).is_loopback:
            raise ValueError
        if set(params) != {"nonce", "instance_id"} or any(len(params.getlist(k)) != 1 for k in params):
            raise ValueError
        nonce, instance = _uuid(params["nonce"]), _uuid(params["instance_id"])
        macs = request.headers.getlist("X-Analytics-Request-MAC")
        body = {"version": 1, "instance_id": instance, "nonce": nonce}
        if (instance != config.instance_id or len(macs) != 1 or not re.fullmatch(r"[0-9a-f]{64}", macs[0])
                or not hmac.compare_digest(macs[0], signature(config.key, REQUEST_DOMAIN, body))):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=401, detail="Analytics source unavailable", headers=headers) from None
    try:
        as_of, events = await run_in_threadpool(read_snapshot, config)
        body = {"version": 1, "instance_id": instance, "nonce": nonce, "as_of": as_of, "events": events}
        return JSONResponse({**body, "mac": signature(config.key, RESPONSE_DOMAIN, body)}, headers=headers)
    except Exception:
        raise HTTPException(status_code=503, detail="Analytics source unavailable", headers=headers) from None
