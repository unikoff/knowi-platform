"""Isolated health apps; no runtime database work or processing at import."""
from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI, Response, status
from sqlalchemy import text

from .config import Settings, core_transfer_config, catalog_config, activity_projection_config, local_deletion_config
from .probes import ReadinessPort, SingleflightReadiness
from .recovery_permit import host_boot_permitted
from .telemetry import Telemetry, IntakeTelemetryMiddleware
from pymentor_analytics.adapters.postgres.database import Database, ClosedPrivacyReadiness
from pymentor_analytics.adapters.auth.config import load_verifiers, load_manifest, validate_authority_key_separation
from pymentor_analytics.modules.collector.application.intake import UnavailableCatalog, UnavailableFence
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.modules.collector.api.middleware import IntakeLimitsMiddleware
from pymentor_analytics.modules.collector.api.router import router as collector_router
from pymentor_analytics.modules.analytics.api.activity_router import router as activity_router
from pymentor_analytics.contracts.v2 import WireBatch


def create_app(role: str, settings: Settings, readiness: ReadinessPort | None = None, *, intake=None, catalog=None, fence=None, processor_store=None, diagnostics=None, activity_reports=None) -> FastAPI:
    if role not in {"collector", "processor", "query", "privacy"}:
        raise ValueError("unknown HTTP role")
    if role == "processor" and (settings.pool_size > 2 or settings.pool_overflow != 0):
        raise ValueError("processor pool exceeds configured bound")
    if settings.real_collection and settings.issuer_mode == "production":
        if role != "collector" or not settings.catalog_enabled or any(x is not None for x in (intake, catalog, fence, readiness)):
            raise ValueError("production browser collection requires configured collector storage and guards")
    if settings.real_collection and role == "collector" and intake is not None:
        raise ValueError("real collection requires its configured intake")
    if diagnostics is not None and role != "processor":
        raise ValueError("diagnostics belong to processor")
    if activity_reports is not None and role != "query":
        raise ValueError("activity reports belong to query")
    core_options = core_transfer_config(settings, role)
    catalog_options = catalog_config(settings, role)
    activity_options = activity_projection_config(settings, role)
    local_source = local_deletion_config(settings, role)
    if activity_options is not None and activity_reports is not None:
        raise ValueError("activity projection cannot use an injected report store")
    telemetry = Telemetry()
    metrics_slot = asyncio.Lock()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        grant_verifier, producer_verifier = load_verifiers(settings, role=role)
        app.state.settings = settings
        app.state.telemetry = telemetry
        app.state.grant_verifier = grant_verifier
        app.state.producer_verifier = producer_verifier
        app.state.trust_manifest_validated = settings.trust_manifest_json is not None
        app.state.trust_manifest_fingerprint = load_manifest(settings.trust_manifest_json)[2] if settings.trust_manifest_json is not None else None
        app.state.catalog = catalog if catalog is not None else UnavailableCatalog()
        app.state.subject_fence = fence if fence is not None else UnavailableFence()
        database = None
        processor_runtime = None
        activity_runtime = None
        activity_runtimes = []
        day_runtime = None
        core_runtime = None
        status_database = None
        deletion_status_database = None
        deletion_relay = None
        privacy_runtime = None
        query_readiness = None
        try:
            if role == "collector" and settings.fixture_catalog_json is not None:
                if catalog is not None:
                    raise ValueError("fixture catalog cannot override another catalog")
                from pymentor_analytics.adapters.catalog.fixture import FixtureCatalog
                app.state.catalog = FixtureCatalog(settings.fixture_catalog_json.get_secret_value())
            database = Database(settings)
            app.state.database = database
            app.state.diagnostics = diagnostics
            if role == "processor" and diagnostics is None:
                from pymentor_analytics.adapters.postgres.quality import PostgresDiagnostics
                app.state.diagnostics = PostgresDiagnostics(database)
            app.state.intake = intake if intake is not None else (
                PostgresIntake(database, subscriptions=()) if role == "collector" and settings.real_collection
                else PostgresIntake(database))
            app.state.probe = readiness if readiness is not None else ClosedPrivacyReadiness(database)
            if settings.privacy_authority_json is not None:
                if readiness is not None:
                    raise ValueError("configured authority cannot use an injected readiness override")
                from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
                from .privacy import PrivacyRuntime
                config = AuthorityConfig.from_json(settings.privacy_authority_json.get_secret_value())
                validate_authority_key_separation(settings, config.public_keys)
                store = None
                if role == "privacy":
                    from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
                    store = PostgresPrivacyStore(database)
                recovery_permit = host_boot_permitted if settings.host_boot_recovery_gate else None
                privacy_runtime = PrivacyRuntime(AuthorityClient(config), database, store=store, recovery_permit=recovery_permit)
                app.state.privacy_runtime = privacy_runtime
                app.state.probe = privacy_runtime
                if role != "privacy":
                    database.transaction_guard = privacy_runtime.check_in_transaction
                if role in {"collector", "query"} and fence is None:
                    from pymentor_analytics.adapters.postgres.privacy import PostgresSubjectFence
                    app.state.subject_fence = PostgresSubjectFence(database)
                privacy_runtime.start()
                if role == "privacy":
                    class PrivacyCapabilityReadiness:
                        async def is_ready(self):
                            if await privacy_runtime.is_ready() is not True:
                                return False
                            try:
                                from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
                                from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema, has_snapshot_proof_schema
                                async with database.sessions.begin() as session:
                                    tables = []
                                    if await has_activity_day_projection_schema(session):
                                        tables.extend(("activity_projection_day_sources", "activity_projection_days"))
                                    if await has_snapshot_proof_schema(session):
                                        tables.append("core_snapshot_proofs")
                                    for table in tables:
                                        ok = (await session.execute(text(
                                            "SELECT has_table_privilege(current_user, :table, 'SELECT') AND "
                                            "has_table_privilege(current_user, :table, 'DELETE')"),
                                            {"table": "analytics." + table})).scalar_one()
                                        if ok is not True:
                                            return False
                                    if await has_core_schema(session):
                                        for table in ("core_transfer_entities", "core_transfer_projection"):
                                            ok = (await session.execute(text(
                                                "SELECT has_table_privilege(current_user, :table, 'SELECT')"),
                                                {"table": "analytics." + table})).scalar_one()
                                            if ok is not True:
                                                return False
                                return True
                            except Exception:
                                return False
                    app.state.probe = PrivacyCapabilityReadiness()
            if local_source is not None:
                from pymentor_analytics.adapters.core_privacy.client import CoreDeletionClient
                source = CoreDeletionClient(local_source)
                if privacy_runtime is None:
                    raise ValueError("local collection requires privacy authority")
                if role in ({"collector", "processor", "query"} if settings.issuer_mode == "production" else {"collector", "query"}):
                    from pymentor_analytics.adapters.core_privacy.guard import CoreDeletionGuard
                    status_settings = settings.model_copy(update={"database_url": settings.core_deletion_status_database_url,
                        "pool_size": 1, "pool_overflow": 0, "db_timeout_seconds": min(settings.db_timeout_seconds, 5)})
                    deletion_status_database = Database(status_settings)
                    source_guard = CoreDeletionGuard(source, deletion_status_database, "core-local")
                    async def combined_guard(session):
                        await privacy_runtime.check_in_transaction(session)
                        await source_guard.check(session)
                        await privacy_runtime.check_in_transaction(session)
                    database.transaction_guard = combined_guard
                    database.transaction_exit_guard = combined_guard
                elif role == "privacy":
                    from pymentor_analytics.adapters.core_privacy.relay import CoreDeletionRelay, RelayConfig
                    relay_config = RelayConfig.from_json(settings.core_deletion_relay_json.get_secret_value())
                    if relay_config.producer != "core-local":
                        raise ValueError("invalid local deletion producer")
                    # The manifest verifies the short-lived JWT against the configured producer ring.
                    grant_verifier_for_relay, relay_verifier, _ = load_manifest(settings.trust_manifest_json)
                    if relay_verifier.verify(relay_config.token()).sub != relay_config.producer:
                        raise ValueError("invalid local deletion producer")
                    from cryptography.hazmat.primitives import serialization
                    def raw_key(key):
                        return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                    authority_keys = {raw_key(serialization.load_pem_public_key(pem.encode("ascii")))
                                      for pem in config.public_keys.values()}
                    grant_keys = {raw_key(key) for key in grant_verifier_for_relay._keys.values()}
                    producer_keys = {raw_key(key) for key in relay_verifier._keys.values()}
                    command = raw_key(relay_config.command_key.public_key())
                    signer = raw_key(relay_config.signing_key.public_key())
                    if command in authority_keys | grant_keys | producer_keys or signer in authority_keys:
                        raise ValueError("invalid local deletion key separation")
                    deletion_relay = CoreDeletionRelay(source, AuthorityClient(config), relay_config,
                                                       recovery_permit=recovery_permit)
                    app.state.core_deletion_relay = deletion_relay
                    deletion_relay.start()
            if core_options is not None:
                from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer, has_core_schema, has_snapshot_proof_schema
                if privacy_runtime is None or database.transaction_guard is None:
                    raise ValueError("Core transfer requires the verified privacy barrier")
                class CoreReadiness:
                    async def is_ready(self):
                        if await privacy_runtime.is_ready() is not True:
                            return False
                        try:
                            async with database.transaction() as session:
                                if not await has_core_schema(session):
                                    return False
                                if settings.core_transfer_proof_enabled and not await has_snapshot_proof_schema(session):
                                    return False
                                table = "core_transfer_entities" if role == "collector" else "core_transfer_projection"
                                await session.execute(text(f"SELECT subject_ref FROM analytics.{table} LIMIT 0"))
                                if settings.core_transfer_proof_enabled:
                                    await session.execute(text("SELECT subject_ref FROM analytics.core_snapshot_proofs LIMIT 0"))
                            if role == "collector":
                                if status_database is None:
                                    return False
                                async with status_database.transaction() as session:
                                    tables = ("activity_facts", "processing_jobs", "processing_receipts", "processing_results",
                                              "processing_leases", "processing_replays", "admission_subject_windows",
                                              "core_transfer_entities", "core_transfer_projection")
                                    if settings.core_transfer_proof_enabled:
                                        tables += ("core_snapshot_proofs",)
                                    from pymentor_analytics.adapters.postgres.activity_projection import has_activity_projection_schema
                                    if await has_activity_projection_schema(session):
                                        tables += ("activity_projection_buckets",)
                                    from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
                                    if await has_activity_day_projection_schema(session):
                                        tables += ("activity_projection_day_sources", "activity_projection_days")
                                    await session.execute(text(" UNION ALL ".join(
                                        f"SELECT subject_ref FROM analytics.{name} WHERE false" for name in tables)))
                                    await session.execute(text("SELECT subject_ref,epoch,deleted FROM analytics.subject_fences LIMIT 0"))
                                    await session.execute(text("SELECT seq,control_id,payload_hash,subject_ref,fence_epoch,phase FROM analytics.privacy_receipts LIMIT 0"))
                                    await session.execute(text("SELECT singleton,authority_id,applied_through FROM analytics.privacy_state LIMIT 0"))
                            return True
                        except Exception:
                            return False
                app.state.core_transfer_probe = CoreReadiness()
                app.state.probe = app.state.core_transfer_probe
                if settings.core_transfer_proof_enabled and role in {"collector", "processor"}:
                    from pymentor_analytics.adapters.postgres.core_snapshot_proof import PostgresSnapshotProof
                    app.state.core_snapshot_proof = PostgresSnapshotProof(database)
                if role == "collector":
                    from pymentor_analytics.modules.analytics.core_transfer.service import TransferService
                    from pymentor_analytics.adapters.postgres.core_transfer import CheckedDeletionStatus
                    if producer_verifier is None or not set(core_options) <= producer_verifier._allowed_producers:
                        raise ValueError("Core producer mapping is outside the verifier allowlist")
                    status_settings = settings.model_copy(update={"database_url": settings.core_transfer_status_database_url,
                        "pool_size": 1, "pool_overflow": 0})
                    status_database = Database(status_settings)
                    # This separate Analytics status connection must obey the same
                    # local source and authority checks as the main reader pool.
                    status_database.transaction_guard = database.transaction_guard
                    status_database.transaction_exit_guard = getattr(database, "transaction_exit_guard", None)
                    app.state.core_transfer_service = TransferService(PostgresCoreTransfer(database))
                    app.state.core_transfer_deletion_status = CheckedDeletionStatus(
                        status_database, AuthorityClient(config),
                        producer_by_namespace={value: key for key, value in core_options.items()})
                elif role == "processor":
                    from .core_transfer_runtime import CoreTransferRuntime
                    core_runtime = CoreTransferRuntime(PostgresCoreTransfer(database), app.state.core_transfer_probe,
                        proof_store=getattr(app.state, "core_snapshot_proof", None),
                        enabled=True, operation_timeout=min(settings.db_timeout_seconds, 30))
                    app.state.core_transfer_runtime = core_runtime
                    core_runtime.start()
                else:
                    from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService
                    app.state.legacy_service = LegacyService(PostgresCoreTransfer(database, browser_run_id=activity_options), core_options["namespace"],
                        core_options["roster"], require_coverage=settings.core_transfer_proof_enabled)
            if catalog_options is not None:
                from pymentor_analytics.adapters.postgres.catalog import PostgresCatalog, has_catalog_schema
                from pymentor_analytics.modules.catalog.service import CatalogService
                if privacy_runtime is None or database.transaction_guard is None or catalog is not None:
                    raise ValueError("catalog requires its verified privacy barrier and exclusive storage")
                if producer_verifier is None or not set(catalog_options["mapping"]) <= producer_verifier._allowed_producers:
                    raise ValueError("catalog producer mapping is outside the verifier allowlist")
                catalog_store = PostgresCatalog(database, catalog_options["namespace"])
                app.state.catalog, app.state.catalog_service = catalog_store, CatalogService(catalog_store)
                class CatalogReadiness:
                    async def is_ready(self):
                        if await privacy_runtime.is_ready() is not True:
                            return False
                        try:
                            async with database.transaction() as session:
                                if not await has_catalog_schema(session):
                                    return False
                                await session.execute(text("SELECT producer_namespace,course_id,catalog_version FROM analytics.catalog_versions LIMIT 0"))
                            return True
                        except Exception:
                            return False
                app.state.catalog_probe = CatalogReadiness()
                if core_options is None:
                    app.state.probe = app.state.catalog_probe
                else:
                    class CombinedReadiness:
                        async def is_ready(self):
                            return (await app.state.core_transfer_probe.is_ready() is True
                                    and await app.state.catalog_probe.is_ready() is True)
                    app.state.probe = CombinedReadiness()
            if activity_options is not None:
                from pymentor_analytics.adapters.postgres.activity_projection import has_activity_projection_schema
                if privacy_runtime is None or database.transaction_guard is None:
                    raise ValueError("activity projection requires the verified privacy barrier")
                class ActivityProjectionReadiness:
                    async def is_ready(self):
                        if role != "processor" and await privacy_runtime.is_ready() is not True:
                            return False
                        try:
                            async with database.transaction() as session:
                                if role == "processor":
                                    await session.execute(text("""SELECT
                                        (SELECT 1 FROM analytics.activity_facts LIMIT 0),
                                        (SELECT 1 FROM analytics.subject_fences LIMIT 0)"""))
                                if not await has_activity_projection_schema(session):
                                    return False
                                if role == "query":
                                    ready = (await session.execute(text(
                                        "SELECT analytics.lock_activity_projection_run(:run)"),
                                        {"run": activity_options})).scalar_one()
                                else:
                                    ready = (await session.execute(text(
                                        "SELECT run_id=:run FROM analytics.activity_projection_backfill WHERE singleton=true"),
                                        {"run": activity_options})).scalar_one_or_none()
                                return ready is True
                        except Exception:
                            return False
                app.state.activity_projection_probe = ActivityProjectionReadiness()
                previous_probe = app.state.probe
                class ProjectionCombinedReadiness:
                    async def is_ready(self):
                        return (await previous_probe.is_ready() is True
                                and await app.state.activity_projection_probe.is_ready() is True)
                app.state.probe = ProjectionCombinedReadiness()
                if role == "processor" and settings.processor_bucket_worker_enabled:
                    from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
                    from .activity_projection_runtime import ActivityProjectionRuntime
                    for _ in range(settings.pool_size):
                        activity_runtime = ActivityProjectionRuntime(
                            PostgresActivityProjection(database, expected_run_id=activity_options),
                            app.state.activity_projection_probe, enabled=True,
                            poll_seconds=settings.processor_activity_poll_seconds,
                            operation_timeout=max(1, min(settings.db_timeout_seconds, 27)))
                        activity_runtimes.append(activity_runtime)
                        activity_runtime.start()
                    activity_runtime = activity_runtimes[0]
                    app.state.activity_projection_runtime = activity_runtime
                    app.state.activity_projection_runtimes = activity_runtimes
            if settings.activity_day_worker_enabled or settings.activity_day_query_enabled:
                from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
                class ActivityDayReadiness:
                    async def is_ready(self):
                        if role != "processor" and await privacy_runtime.is_ready() is not True:
                            return False
                        try:
                            async with database.transaction() as session:
                                if role == "processor":
                                    await session.execute(text("""SELECT
                                        (SELECT 1 FROM analytics.activity_facts LIMIT 0),
                                        (SELECT 1 FROM analytics.subject_fences LIMIT 0)"""))
                                from pymentor_analytics.adapters.postgres.activity_projection import has_activity_projection_schema
                                if (not await has_activity_projection_schema(session)
                                        or not await has_activity_day_projection_schema(session)):
                                    return False
                                if settings.activity_course_worker_enabled or settings.activity_course_query_enabled:
                                    from pymentor_analytics.adapters.postgres.activity_course_projection import has_activity_course_projection_schema
                                    if not await has_activity_course_projection_schema(session):
                                        return False
                                if settings.activity_boundary_credit_enabled:
                                    available = (await session.execute(text("""SELECT
                                        to_regclass('analytics.activity_credit_windows') IS NOT NULL AND
                                        to_regclass('analytics.activity_credit_days') IS NOT NULL"""))).scalar_one()
                                    if not available:
                                        return False
                                # a010 complete is necessary for both the scan and reads.
                                if role == "query":
                                    ready = (await session.execute(text(
                                        "SELECT analytics.lock_activity_projection_run(:run)"),
                                        {"run": activity_options})).scalar_one()
                                else:
                                    # Processor can read the backfill state but cannot execute
                                    # the query-only shared-lock helper. The store locks on work.
                                    ready = (await session.execute(text(
                                        "SELECT run_id=:run AND phase='complete' FROM analytics.activity_projection_backfill WHERE singleton=true"),
                                        {"run": activity_options})).scalar_one_or_none()
                                if ready is not True:
                                    return False
                                for table in ("activity_projection_day_sources", "activity_projection_days", "activity_projection_day_backfill"):
                                    await session.execute(text(f"SELECT * FROM analytics.{table} LIMIT 0"))
                                if role == "query":
                                    ready = (await session.execute(text(
                                        "SELECT run_id=:run AND phase='complete' FROM analytics.activity_projection_day_backfill WHERE singleton=true"),
                                        {"run": activity_options})).scalar_one_or_none()
                                    return ready is True
                                checks = (
                                    "has_table_privilege(current_user, 'analytics.activity_projection_days', 'INSERT')",
                                    "has_table_privilege(current_user, 'analytics.activity_projection_days', 'UPDATE')",
                                    *[f"has_column_privilege(current_user, 'analytics.activity_projection_day_sources', '{col}', 'UPDATE')" for col in
                                      ("state", "claim_id", "owner", "lease_expires_at", "claim_generation", "claim_epoch", "claim_run_id", "attempts", "next_attempt_at", "last_failure")],
                                    *[f"has_column_privilege(current_user, 'analytics.activity_projection_day_backfill', '{col}', 'UPDATE')" for col in
                                      ("phase", "cursor_subject", "cursor_bucket", "cursor_metric")],
                                    *[f"has_function_privilege(current_user, 'analytics.{signature}', 'EXECUTE')" for signature in
                                      ("register_activity_day(uuid,timestamptz,text,uuid)", "begin_activity_day_backfill(uuid)", "invalidate_stale_activity_day(uuid,date,uuid)")],
                                )
                                return (await session.execute(text("SELECT " + " AND ".join(checks)))).scalar_one() is True
                        except Exception:
                            return False
                app.state.activity_day_probe = ActivityDayReadiness()
                if (role == "query" or (role == "processor"
                        and not settings.processor_bucket_worker_enabled
                        and not settings.processor_generic_worker_enabled)):
                    prior_day_probe = app.state.probe
                    class DayCombinedReadiness:
                        async def is_ready(self):
                            return (await prior_day_probe.is_ready() is True
                                    and await app.state.activity_day_probe.is_ready() is True)
                    app.state.probe = DayCombinedReadiness()
                if role == "processor":
                    from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
                    from .activity_day_projection_runtime import ActivityDayProjectionRuntime
                    course_backfill = None
                    if settings.activity_course_worker_enabled:
                        from pymentor_analytics.adapters.postgres.activity_course_projection import backfill_course_projection_step
                        async def course_backfill(*, deadline):
                            phase = await backfill_course_projection_step(database, run_id=activity_options, deadline=deadline)
                            if phase in {"bucket", "day"} or not settings.activity_boundary_credit_enabled:
                                return phase
                            from pymentor_analytics.adapters.postgres.activity_boundary_credit import boundary_credit_step
                            return await boundary_credit_step(database, run_id=activity_options, deadline=deadline)
                    day_runtime = ActivityDayProjectionRuntime(
                        PostgresActivityDayProjection(database, expected_run_id=activity_options),
                        app.state.activity_day_probe, enabled=True, course_backfill=course_backfill,
                        poll_seconds=settings.processor_activity_poll_seconds)
                    app.state.activity_day_runtime = day_runtime
                    day_runtime.start()
            if role == "query":
                from pymentor_analytics.adapters.postgres.activity_reports import PostgresActivityReport
                if activity_options is not None:
                    from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
                    app.state.activity_reports = PostgresProjectionActivityReport(
                        database, app.state.subject_fence, expected_run_id=activity_options,
                        day_projection_enabled=settings.activity_day_query_enabled,
                        course_projection_enabled=settings.activity_course_query_enabled,
                        pending_wait_seconds=min(2.0, settings.db_timeout_seconds / 2))
                else:
                    app.state.activity_reports = activity_reports if activity_reports is not None else PostgresActivityReport(database, app.state.subject_fence)
            if role == "processor" and settings.processor_generic_worker_enabled:
                from .processor import ProcessorRuntime
                if processor_store is None:
                    from pymentor_analytics.adapters.postgres.processor import PostgresProcessor
                    store = PostgresProcessor(database)
                else:
                    store = processor_store
                processor_runtime = ProcessorRuntime(store, app.state.probe, settings)
                app.state.processor_runtime = processor_runtime
                processor_runtime.start()
            if local_source is not None and role in ({"collector", "processor", "query"} if settings.issuer_mode == "production" else {"collector", "query"}):
                previous_local_probe = app.state.probe
                class LocalDeletionReadiness:
                    async def is_ready(self):
                        if await previous_local_probe.is_ready() is not True:
                            return False
                        try:
                            async with database.transaction():
                                pass
                            return True
                        except Exception:
                            return False
                app.state.probe = LocalDeletionReadiness()
            if role == "query" and activity_options is not None:
                query_readiness = SingleflightReadiness(app.state.probe, settings.db_timeout_seconds)
                app.state.probe = query_readiness
            yield
        finally:
            try:
                if query_readiness is not None:
                    await query_readiness.close()
                if deletion_relay is not None:
                    await deletion_relay.stop()
                if hasattr(app.state, "core_deletion_relay"):
                    del app.state.core_deletion_relay
                if deletion_status_database is not None:
                    await deletion_status_database.close()
                if day_runtime is not None:
                    await day_runtime.stop()
                for attribute in ("activity_day_runtime", "activity_day_probe"):
                    if hasattr(app.state, attribute):
                        delattr(app.state, attribute)
                if activity_runtimes:
                    results = await asyncio.gather(*(worker.stop() for worker in activity_runtimes), return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                for attribute in ("activity_projection_runtime", "activity_projection_runtimes", "activity_projection_probe"):
                    if hasattr(app.state, attribute):
                        delattr(app.state, attribute)
                if core_runtime is not None:
                    await core_runtime.stop()
                if hasattr(app.state, "core_transfer_runtime"):
                    del app.state.core_transfer_runtime
                if status_database is not None:
                    await status_database.close()
                for attribute in ("core_transfer_probe", "core_transfer_service", "core_transfer_deletion_status", "core_snapshot_proof", "legacy_service", "catalog_service", "catalog_probe"):
                    if hasattr(app.state, attribute):
                        delattr(app.state, attribute)
                if processor_runtime is not None:
                    await processor_runtime.stop()
            finally:
                if hasattr(app.state, "processor_runtime"):
                    del app.state.processor_runtime
                try:
                    if privacy_runtime is not None:
                        await privacy_runtime.stop()
                finally:
                    if database is not None:
                        await database.close()
                if hasattr(app.state, "privacy_runtime"):
                    del app.state.privacy_runtime
                if hasattr(app.state, "database"):
                    del app.state.database
                if hasattr(app.state, "probe"):
                    del app.state.probe
                del app.state.grant_verifier
                del app.state.producer_verifier
                del app.state.trust_manifest_validated
                del app.state.trust_manifest_fingerprint
                if hasattr(app.state, "intake"):
                    del app.state.intake
                del app.state.catalog
                del app.state.subject_fence
                del app.state.settings
                del app.state.telemetry
                if hasattr(app.state, "diagnostics"):
                    del app.state.diagnostics
                if hasattr(app.state, "activity_reports"):
                    del app.state.activity_reports

    app = FastAPI(title=f"Analytics {role}", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    class StateProxy:
        def __init__(self, attribute): self.attribute = attribute
        def __getattr__(self, name): return getattr(getattr(app.state, self.attribute), name)
    if role == "query":
        app.include_router(activity_router)
        from pymentor_analytics.modules.analytics.api.course_visitors_router import router as course_visitors_router
        app.state.course_visitor_slots = asyncio.Semaphore(min(settings.concurrency, 4))
        app.include_router(course_visitors_router)
        from pymentor_analytics.modules.analytics.api.measured_calendar_router import router as measured_calendar_router
        app.include_router(measured_calendar_router)
        if core_options is not None:
            from pymentor_analytics.modules.analytics.core_transfer.api.legacy_router import router as legacy_router
            app.include_router(legacy_router)
    if role == "collector":
        app.add_middleware(IntakeLimitsMiddleware, concurrency=settings.concurrency, timeout_seconds=settings.db_timeout_seconds)
        app.add_middleware(IntakeTelemetryMiddleware, telemetry=telemetry)
        if settings.issuer_mode != "production" or settings.real_collection:
            app.include_router(collector_router)
        if core_options is not None:
            from pymentor_analytics.modules.analytics.core_transfer.api.router import create_router
            app.include_router(create_router(service=StateProxy("core_transfer_service"),
                verifier=StateProxy("producer_verifier"), namespace_by_producer=core_options,
                readiness=StateProxy("core_transfer_probe"), deletion_status=StateProxy("core_transfer_deletion_status"),
                proof_service=StateProxy("core_snapshot_proof") if settings.core_transfer_proof_enabled else None,
                timeout_seconds=settings.db_timeout_seconds, concurrency=settings.concurrency))
        if catalog_options is not None:
            from pymentor_analytics.modules.catalog.api.router import create_router as create_catalog_router
            app.include_router(create_catalog_router(service=StateProxy("catalog_service"),
                verifier=StateProxy("producer_verifier"), namespace_by_producer=catalog_options["mapping"],
                readiness=StateProxy("catalog_probe"), timeout_seconds=settings.db_timeout_seconds,
                concurrency=settings.concurrency))
    if role in {"collector", "query"}:
        default_openapi = app.openapi
        def collector_openapi():
            schema = default_openapi()
            components = schema.setdefault("components", {})
            if role == "collector":
                components.setdefault("schemas", {})["WireBatch"] = WireBatch.model_json_schema()
            components.setdefault("securitySchemes", {})["AnalyticsGrant"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            return schema
        app.openapi = collector_openapi

    @app.get("/live", include_in_schema=False)
    async def live():
        return {"status": "live"}

    @app.get("/ready", include_in_schema=False)
    async def ready(response: Response):
        if app.state.trust_manifest_fingerprint is not None:
            response.headers["X-Analytics-Trust-Fingerprint"] = app.state.trust_manifest_fingerprint
        try:
            is_ready = await asyncio.wait_for(app.state.probe.is_ready(), timeout=settings.db_timeout_seconds)
        except Exception:
            is_ready = False
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if is_ready else "unavailable"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        # Private operational route, denied by public ingress. One bounded
        # scrape per process; it never exposes subject/course/event labels.
        if metrics_slot.locked():
            return Response("metrics busy\n", status_code=503, media_type="text/plain")
        async with metrics_slot:
            try:
                ready_value = await asyncio.wait_for(app.state.probe.is_ready(), timeout=min(1,settings.db_timeout_seconds))
            except Exception:
                ready_value = False
            snapshot = None
            if app.state.diagnostics is not None:
                try:
                    snapshot = await asyncio.wait_for(app.state.diagnostics.read(), timeout=min(1,settings.db_timeout_seconds))
                except Exception:
                    pass
            try:
                pool = app.state.database.pool_snapshot()
            except Exception:
                pool = None
            try:
                output = telemetry.render(role, ready_value, snapshot, pool)
            except (ValueError, TypeError, AttributeError):
                output = telemetry.render(role, ready_value, None, pool)
            if role == "processor":
                for lane, attribute in (("bucket", "activity_projection_runtime"), ("day", "activity_day_runtime")):
                    worker = getattr(app.state, attribute, None)
                    if lane == "bucket" and worker is not None:
                        counts = [getattr(item, "backfill_failures", None) for item in app.state.activity_projection_runtimes]
                        count = min(sum(counts), 2**63 - 1) if all(type(value) is int and 0 <= value <= 2**63 - 1 for value in counts) else None
                    else:
                        count = getattr(worker, "backfill_failures", None)
                    if type(count) is int and 0 <= count <= 2**63 - 1:
                        output += f'analytics_activity_projection_backfill_failures_total{{lane="{lane}"}} {count}\n'
            return Response(output, media_type="text/plain; version=0.0.4")

    return app
