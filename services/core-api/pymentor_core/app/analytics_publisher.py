"""Separate, explicitly enabled Core Analytics publisher; never started by ASGI."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import random
import signal

LOG = logging.getLogger("core.analytics.publisher")


@dataclass(frozen=True)
class Config:
    url: object = field(repr=False)
    expected_database: str
    expected_user: str
    interval: float = 5.0


def config_from_env(env):
    # All admission checks precede engine creation, credentials or network I/O.
    for name in ("PYMENTOR_ANALYTICS_PUBLISHER_ENABLED", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED",
                 "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE"):
        if env.get(name) != "1":
            raise ValueError("publisher admission disabled or incomplete")
    from pymentor_core.app.settings import postgres_database_url
    url = postgres_database_url(env.get("DATABASE_URL", "")).set(drivername="postgresql+psycopg")
    expected_database = env.get("PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE", "")
    expected_user = env.get("PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER", "")
    if not expected_database or not expected_user or (url.database, url.username) != (expected_database, expected_user):
        raise ValueError("publisher database identity mismatch")
    if env.get("PYMENTOR_SCHEMA_MODE") not in (None, "managed") or env.get("PYMENTOR_DUAL_LOCAL_TEST") == "1":
        raise ValueError("publisher requires managed schema")
    if env.get("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror") not in {"mirror", "outbox_only"}:
        raise ValueError("publisher write mode unavailable")
    if env.get("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror") == "outbox_only" and env.get("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "off") != "analytics":
        raise ValueError("publisher write mode unavailable")
    return Config(url, expected_database, expected_user)


def validate_database(engine, config):
    from sqlalchemy import text
    from pymentor_core.app.schema_policy import SCHEMA_REVISION, compatible_schema_revision
    from pymentor_core.adapters.sql.analytics_delivery_ops.addon_migration import check_version
    from pymentor_core.adapters.sql.analytics_delivery_ops.integration import ensure_mirror_ready, require_write_gate, task_enabled, write_mode
    from sqlalchemy.orm import Session
    with engine.connect() as connection:
        actual = tuple(connection.execute(text("SELECT current_database(), current_user")).one())
        if actual != (config.expected_database, config.expected_user):
            raise ValueError("publisher database identity mismatch")
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if not compatible_schema_revision(revision, SCHEMA_REVISION):
            raise ValueError("publisher Core schema unavailable")
        check_version(connection, 2)
        sealed = connection.execute(text("""SELECT b.stream,b.high_watermark,c.high_watermark
            FROM analytics_transfer_snapshot_barriers b
            JOIN analytics_transfer_checkpoints c ON c.stream=b.stream""")).all()
        required = {"activity", "progress"} | ({"tasks"} if task_enabled() else set())
        relevant = [row for row in sealed if row[0] in required]
        if {row[0] for row in relevant} != required or any(row[1] != row[2] for row in relevant):
            raise ValueError("publisher backfill is not sealed")
    with Session(engine) as db:
        mode = write_mode()
        require_write_gate(db, mode)
        if mode == "mirror":
            ensure_mirror_ready(db)


def build_clients(env):
    """Keep independent authority, producer and grant signing boundaries."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key, Encoding, PublicFormat
    from pymentor_core.adapters.analytics_http.privacy import AuthorityClient
    from pymentor_core.adapters.analytics_http.transport import TransferHttp
    from pymentor_core.modules.analytics_delivery.signing import Signer
    signer = Signer(issuer=env["PYMENTOR_ANALYTICS_PRODUCER_ISSUER"], kid=env["PYMENTOR_ANALYTICS_PRODUCER_KID"],
                    private_pem=Path(env["PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE"]).read_bytes(), purpose="producer")
    command = load_pem_private_key(Path(env["PYMENTOR_ANALYTICS_COMMAND_KEY_FILE"]).read_bytes(), password=None)
    grant = load_pem_public_key(Path(env["PYMENTOR_ANALYTICS_GRANT_PUBLIC_KEY_FILE"]).read_bytes())
    authority_pem = Path(env["PYMENTOR_ANALYTICS_AUTHORITY_PUBLIC_KEY_FILE"]).read_bytes()
    authority_key = load_pem_public_key(authority_pem)
    keys = [key.public_bytes(Encoding.Raw, PublicFormat.Raw) for key in
            (signer._key.public_key(), command.public_key(), grant, authority_key)]
    if len(set(keys)) != 4:
        raise ValueError("signing keys must be independent")
    producer = env["PYMENTOR_ANALYTICS_PRODUCER_ID"]
    token = lambda: signer.producer(producer)
    from pymentor_core.adapters.analytics_http.http import client_tls
    transfer_url = env["PYMENTOR_ANALYTICS_TRANSFER_URL"]
    transport = TransferHttp(transfer_url, token, verify=client_tls(env, "PYMENTOR_ANALYTICS_TRANSFER", transfer_url))
    authority = AuthorityClient(endpoint=env["PYMENTOR_ANALYTICS_AUTHORITY_URL"],
        authority_id=env["PYMENTOR_ANALYTICS_AUTHORITY_ID"], issuer=env["PYMENTOR_ANALYTICS_AUTHORITY_ISSUER"],
        public_keys={env["PYMENTOR_ANALYTICS_AUTHORITY_KID"]: authority_pem},
        producer=producer, producer_token=token, command_key=command, verify=authority_tls(env))
    return transport, authority


def authority_tls(env):
    """Require explicit mutual TLS credentials for a non-loopback authority."""
    from pymentor_core.adapters.analytics_http.http import client_tls
    return client_tls(env, "PYMENTOR_ANALYTICS_AUTHORITY", env["PYMENTOR_ANALYTICS_AUTHORITY_URL"])


async def run(sessions, transport, authority, stop, *, interval=5.0):
    from sqlalchemy.exc import SQLAlchemyError
    from pymentor_core.adapters.sql.analytics_delivery_ops.worker import run_once as relay_facts
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_relay import run_once as relay_proofs
    from pymentor_core.adapters.sql.analytics_delivery_ops.publisher_scan import scan_page
    cursor, failures = 0, 0
    while not stop.is_set():
        try:
            check_gate(sessions)
            facts = await relay_facts(sessions, transport, authority=authority, batch_size=10, deadline_seconds=5)
            if stop.is_set():
                break
            scan = scan_page(sessions, cursor, page_size=20, stage_limit=2)
            cursor = scan.cursor
            if stop.is_set():
                break
            proofs = await relay_proofs(sessions, transport, limit=10, deadline_seconds=5)
            details = ""
            try:
                backlog = backlog_snapshot(sessions)
            except SQLAlchemyError:
                fact_blocked, proof_blocked = blocked_backlog(sessions)
                LOG.warning("publisher_backlog_detail_unavailable")
            else:
                fact_blocked, proof_blocked = backlog["fact_blocked"], backlog["proof_blocked"]
                names = ("pending", "claimed", "quarantined", "eligible_sample", "truncated")
                details = f" backlog_cap={BACKLOG_CAP}" + "".join(
                    f" {kind}_{name}={int(backlog[f'{kind}_{name}'])}"
                    for kind in ("fact", "proof") for name in names
                )
            failures = 0
            LOG.info("publisher_tick facts_claimed=%d scanned=%d staged=%d blocked=%d proofs_claimed=%d "
                     "fact_blocked=%d proof_blocked=%d%s",
                     facts, scan.scanned, scan.staged, scan.blocked, proofs, fact_blocked, proof_blocked, details)
            delay = interval if not (scan.blocked or fact_blocked or proof_blocked) else max(interval, 10.0)
        except Exception:
            failures = min(failures + 1, 6)
            # Do not print exceptions: database/transport errors can include secrets.
            LOG.warning("publisher_cycle_failed consecutive=%d", failures)
            delay = min(60.0, interval * (2 ** failures))
        if not stop.is_set():
            delay = min(60.0, delay * random.uniform(0.8, 1.2))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    LOG.info("publisher_stopped")


def check_gate(sessions):
    from pymentor_core.adapters.sql.analytics_delivery_ops.integration import ensure_mirror_ready, require_write_gate, write_mode
    with sessions() as db:
        mode = write_mode()
        require_write_gate(db, mode)
        if mode == "mirror":
            ensure_mirror_ready(db)


def blocked_backlog(sessions):
    """Aggregate only; a terminal claim needs operator reconciliation, not replay."""
    from sqlalchemy import text
    with sessions() as db:
        return tuple(db.execute(text("""SELECT
            EXISTS (SELECT 1 FROM analytics_transfer_outbox
                    WHERE status='quarantined' OR (status IN ('pending','claimed') AND attempts>=20)),
            EXISTS (SELECT 1 FROM analytics_transfer_snapshot_proofs
                    WHERE status='quarantined' OR (status IN ('pending','claimed') AND attempts>=30))""")).one())


BACKLOG_CAP = 1000


def backlog_snapshot(sessions):
    """Read one snapshot, capped per queue/status; sampled eligibility is a lower bound.

    Terminal flags retain the original exact EXISTS checks. The status samples use
    the existing (status, next_attempt_at) indexes and never return identifiers.
    """
    from datetime import datetime, timezone
    from sqlalchemy import and_, case, exists, func, or_, select
    from pymentor_core.adapters.sql.analytics_delivery import outbox
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import proofs

    now = datetime.now(timezone.utc)
    columns = []
    for kind, table, max_attempts in (("fact", outbox, 20), ("proof", proofs, 30)):
        eligible_parts = []
        for status in ("pending", "claimed", "quarantined"):
            fields = [table.c.status, table.c.attempts, table.c.next_attempt_at, table.c.lease_until]
            if kind == "proof" and status != "quarantined":
                fields.extend((table.c.subject_ref, table.c.cut_revision))
            sample = select(*fields).where(table.c.status == status).limit(BACKLOG_CAP + 1).cte(f"{kind}_{status}_sample")
            columns.append(select(func.count()).select_from(sample).scalar_subquery().label(f"{kind}_{status}"))
            if status != "quarantined":
                due = and_(sample.c.attempts < max_attempts, sample.c.next_attempt_at <= now,
                           or_(sample.c.status == "pending", sample.c.lease_until <= now))
                if kind == "proof":
                    outstanding = exists(select(1).select_from(outbox).where(
                        outbox.c.subject_ref == sample.c.subject_ref,
                        outbox.c.kind != "subject_deleted", outbox.c.revision <= sample.c.cut_revision,
                        outbox.c.status != "delivered"))
                    due = and_(due, ~outstanding)
                eligible_parts.append(select(func.coalesce(func.sum(case((due, 1), else_=0)), 0))
                                      .select_from(sample).scalar_subquery())
        columns.append((eligible_parts[0] + eligible_parts[1]).label(f"{kind}_eligible_sample"))
        columns.append(exists(select(1).select_from(table).where(or_(
            table.c.status == "quarantined",
            and_(table.c.status.in_(("pending", "claimed")), table.c.attempts >= max_attempts))))
            .label(f"{kind}_blocked"))
    with sessions() as db:
        result = dict(db.execute(select(*columns)).mappings().one())
    for kind in ("fact", "proof"):
        result[f"{kind}_truncated"] = any(result[f"{kind}_{status}"] > BACKLOG_CAP
                                            for status in ("pending", "claimed", "quarantined"))
        for status in ("pending", "claimed", "quarantined"):
            result[f"{kind}_{status}"] = min(result[f"{kind}_{status}"], BACKLOG_CAP)
    return result


def main():
    config = config_from_env(os.environ)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    engine = create_engine(config.url, poolclass=NullPool, hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000 -c lock_timeout=1000"})
    try:
        validate_database(engine, config)
        transport, authority = build_clients(os.environ)
        stop = asyncio.Event()
        def request_stop(_signum, _frame):
            stop.set()
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        LOG.info("publisher_ready")
        asyncio.run(run(sessionmaker(engine, autoflush=False), transport, authority, stop, interval=config.interval))
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        main()
    except Exception:
        raise SystemExit("Publisher admission or runtime failed; inspect configuration and guarded state") from None
