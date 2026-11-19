"""Single PostgreSQL/Redis HTTP assembly; resource I/O belongs to lifespan."""

import os
import sys
from contextlib import asynccontextmanager

from pymentor_core.app.settings import Settings
from pymentor_core.app.schema_policy import SCHEMA_REVISION
from pymentor_core.app.schema_policy import compatible_schema_revision
from pymentor_core.app.schema_policy import REQUIRED_TABLES


def create_app(profile=None, *, laboratory=False):
    """Build the only supported Core runtime without connecting on import."""
    if laboratory:
        raise ValueError("Laboratory startup is not a Core runtime")
    settings = Settings.from_environment(profile)
    if settings.course_transport != "local":
        raise RuntimeError("HTTP course transport is implemented separately in MC2")
    existing = sys.modules.get("pymentor_core.app.database")
    if existing is not None:
        from sqlalchemy.engine import make_url

        if existing.engine.url != make_url(settings.synchronous_database_url):
            raise RuntimeError("A different database identity requires a fresh process")
    os.environ["DATABASE_URL"] = settings.synchronous_database_url
    os.environ["PYMENTOR_SCHEMA_MODE"] = "managed"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PYMENTOR_PROFILE"] = settings.profile
    from fastapi import FastAPI
    from fastapi import HTTPException
    from fastapi import Request
    from sqlalchemy import inspect
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError
    from pymentor_core.app.wiring import configure_bindings

    configure_bindings()
    from pymentor_core.app.wiring import build_default_composition
    from pymentor_core.app.web import configure_web
    from pymentor_core.app.web import mount_frontend
    from pymentor_core.app.database import engine
    from pymentor_core.app.database import matches_database_identity
    from pymentor_core.modules.administration.api.router import admin_router
    from pymentor_core.modules.administration.api.role_admin_router import router as role_admin_router
    from pymentor_core.modules.identity.api.router import user_router
    from pymentor_core.modules.bookings.api.router import booking_router
    from pymentor_core.modules.learning.api.router import learning_router
    from pymentor_core.modules.landings.api.public import (
        router as public_landing_router,
    )

    @asynccontextmanager
    async def application_lifespan(application):
        from pymentor_core.app.wiring import postgres_resources

        try:
            async with postgres_resources(application, settings):
                if application.state.analytics_local_collection is not None:
                    from starlette.concurrency import run_in_threadpool
                    from pymentor_core.modules.analytics_delivery.local_collection import read_snapshot
                    try:
                        await run_in_threadpool(read_snapshot, application.state.analytics_local_collection)
                    except Exception:
                        raise RuntimeError("Local Analytics source unavailable") from None
                application.state.startup_complete = True
                try:
                    yield
                finally:
                    application.state.startup_complete = False
        finally:
            engine.dispose()

    app = FastAPI(lifespan=application_lifespan)
    from pymentor_core.modules.identity.cache_session_marker import new_marker_key
    app.state.calendar_session_marker_key = new_marker_key()
    app.state.profile = settings.profile
    app.state.settings = settings
    from pymentor_core.modules.analytics_delivery.integration import write_mode as analytics_write_mode
    app.state.analytics_legacy_write_mode = analytics_write_mode()
    if os.environ.get("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED") == "1" and os.environ.get("PYMENTOR_ANALYTICS_DELIVERY_ENABLED") != "1":
        raise RuntimeError("Analytics snapshot proof requires delivery capture")
    app.state.startup_complete = False
    app.state.composition_root = build_default_composition()
    configure_web(app)
    from pymentor_core.app.release import ReleaseRevisionMiddleware
    app.add_middleware(ReleaseRevisionMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/health/live")
    def liveness_health():
        # Keep liveness independent of the database and other dependencies so a
        # transient outage does not create a restart loop.
        return {"status": "ok"}

    @app.get("/health/startup")
    def startup_health():
        if not app.state.startup_complete:
            raise HTTPException(status_code=503, detail="Core startup incomplete")
        return {"status": "started"}

    @app.get("/health/ready")
    def readiness_health(request: Request):
        if not app.state.startup_complete:
            raise HTTPException(status_code=503, detail="Core startup incomplete")
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                identity = tuple(
                    connection.execute(
                        text("SELECT current_database(), current_user")
                    ).one()
                )
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                available = set(inspect(connection).get_table_names())
                if (
                    not matches_database_identity(identity, engine.url)
                    or not compatible_schema_revision(revision, SCHEMA_REVISION)
                    or not set(REQUIRED_TABLES).issubset(available)
                ):
                    raise HTTPException(
                        status_code=503, detail="Configured database schema unavailable"
                    )
                from anyio import from_thread
                from pymentor_core.modules.identity.contracts import (
                    IdentityUnavailable,
                )
                from redis.exceptions import RedisError

                session_service = getattr(
                    request.app.state, "identity_sessions", None
                )
                if session_service is None:
                    raise HTTPException(
                        status_code=503, detail="Session storage unavailable"
                    )
                try:
                    from_thread.run(session_service.check_async_database)
                    if not from_thread.run(session_service.redis.ping):
                        raise HTTPException(
                            status_code=503, detail="Session storage unavailable"
                        )
                except (IdentityUnavailable, RedisError, TimeoutError) as exc:
                    raise HTTPException(
                        status_code=503, detail="Session storage unavailable"
                    ) from exc
        except SQLAlchemyError:
            raise HTTPException(
                status_code=503, detail="database unavailable"
            ) from None
        return {"status": "ready"}

    app.include_router(admin_router, prefix="/admin")
    app.include_router(user_router, prefix="/user")
    app.include_router(booking_router, prefix="/user")
    app.include_router(learning_router, prefix="/learning")
    app.include_router(
        public_landing_router, prefix="/api/landings", tags=["landing-public"]
    )
    app.include_router(admin_router, prefix="/api/admin")
    app.include_router(role_admin_router, prefix="/api")
    app.include_router(user_router, prefix="/api/user")
    app.include_router(booking_router, prefix="/api/user")
    app.include_router(learning_router, prefix="/api/learning")
    if os.environ.get("PYMENTOR_ANALYTICS_DELIVERY_ENABLED") == "1":
        # Feature-on deletion has a durable pending state; keep the legacy schema
        # untouched when delivery is disabled.
        original_openapi = app.openapi
        def analytics_delivery_openapi():
            schema = original_openapi()
            for path in ("/admin/users/{user_id}", "/api/admin/users/{user_id}"):
                operation = schema["paths"][path]["delete"]
                for parameter in operation.get("parameters", []):
                    if parameter.get("in") == "header" and parameter.get("name") == "X-Account-Ref":
                        parameter["required"] = True
                responses = operation["responses"]
                responses.pop("200", None)
                responses["202"] = {"description": "Core deletion committed; Analytics purge is pending.",
                    "headers": {"Retry-After": {"schema": {"type": "string"}}},
                    "content": {"application/json": {"schema": {"type": "object", "additionalProperties": False,
                        "required": ["status"], "properties": {"status": {"const": "pending"}}}}}}
                responses["204"] = {"description": "Analytics deletion fence and purge have been confirmed."}
                responses["503"] = {"description": "Durable deletion status unavailable."}
            return schema
        app.openapi = analytics_delivery_openapi
    if os.environ.get("PYMENTOR_ANALYTICS_GRANTS_ENABLED") == "1":
        from pathlib import Path
        from pymentor_core.modules.analytics_delivery.signing import Signer
        from pymentor_core.modules.analytics_delivery.router import router as analytics_grant_router
        try:
            key_path = Path(os.environ["PYMENTOR_ANALYTICS_GRANT_KEY_FILE"])
            app.state.analytics_grant_signer = Signer(
                issuer=os.environ["PYMENTOR_ANALYTICS_GRANT_ISSUER"],
                kid=os.environ["PYMENTOR_ANALYTICS_GRANT_KID"], private_pem=key_path.read_bytes())
        except (KeyError, OSError, ValueError):
            raise RuntimeError("Analytics grant signing is not configured") from None
        app.include_router(analytics_grant_router, prefix="/api")
    if os.environ.get("PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED") == "1":
        from pymentor_core.modules.learning.api.analytics_catalog_router import analytics_catalog_router
        app.include_router(analytics_catalog_router, prefix="/api")
    legacy_read_mode = os.environ.get("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "off")
    if legacy_read_mode not in {"off", "shadow", "analytics"}:
        raise ValueError("Invalid Analytics legacy read mode")
    browser_presence_required = os.environ.get("PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED", "0")
    if browser_presence_required not in {"0", "1"}:
        raise ValueError("Invalid Analytics browser presence requirement")
    if browser_presence_required == "1" and legacy_read_mode != "analytics":
        raise ValueError("Analytics browser presence requires Analytics legacy reads")
    app.state.analytics_legacy_read_mode = legacy_read_mode
    app.state.analytics_browser_presence_required = browser_presence_required == "1"
    from pymentor_core.modules.analytics_delivery.local_collection import configure as configure_local_collection
    app.state.analytics_local_collection = configure_local_collection(os.environ, settings.synchronous_database_url)
    retire_legacy_visits = os.environ.get("PYMENTOR_ANALYTICS_RETIRE_LEGACY_VISITS", "0")
    if retire_legacy_visits not in {"0", "1"} or (retire_legacy_visits == "1" and (
            app.state.analytics_local_collection is None
            or os.environ.get("PYMENTOR_ANALYTICS_LOCAL_TRANSFER_ENABLED") != "1"
            or legacy_read_mode != "analytics" or browser_presence_required != "1"
            or os.environ.get("PYMENTOR_ANALYTICS_LEARNER_READS_ENABLED") != "1")):
        raise ValueError("Legacy visit retirement requires the local Analytics cutover configuration")
    app.state.analytics_retire_legacy_visits = retire_legacy_visits == "1"
    if app.state.analytics_local_collection is not None:
        from hashlib import sha256
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
        grant_key = app.state.analytics_grant_signer._key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        source_key = app.state.analytics_local_collection.key
        source_public = Ed25519PrivateKey.from_private_bytes(source_key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        if (grant_key == source_key or sha256(source_public).hexdigest()
                == app.state.analytics_local_collection.catalog_binding.producer_sha256):
            raise ValueError("Local Analytics source key must be independent")
        from pymentor_core.modules.analytics_delivery.local_collection import router as local_collection_router
        app.include_router(local_collection_router, prefix="/api")
    if os.environ.get("PYMENTOR_ANALYTICS_LEARNER_READS_ENABLED") == "1":
        if not hasattr(app.state, "analytics_grant_signer"):
            raise RuntimeError("Learner Analytics reads require grant signing")
        from pymentor_core.adapters.analytics_http.learner_read import LearnerAnalyticsClient
        try:
            app.state.analytics_learner_client = LearnerAnalyticsClient(os.environ["PYMENTOR_ANALYTICS_QUERY_URL"])
        except (KeyError, ValueError):
            raise RuntimeError("Learner Analytics query endpoint is not configured") from None
    if legacy_read_mode != "off":
        if not hasattr(app.state, "analytics_grant_signer"):
            raise RuntimeError("Analytics legacy reads require grant signing")
        from pymentor_core.adapters.analytics_http.legacy_read import LegacyReadClient
        try:
            app.state.analytics_legacy_client = LegacyReadClient(os.environ["PYMENTOR_ANALYTICS_QUERY_URL"])
        except (KeyError, ValueError):
            raise RuntimeError("Analytics legacy query endpoint is not configured") from None
        app.state.analytics_legacy_shadow = {}
        prior_openapi = app.openapi
        def legacy_read_openapi():
            schema = prior_openapi()
            for path in ("/user/activity", "/api/user/activity", "/user/activity/summary",
                         "/api/user/activity/summary", "/admin/overview", "/api/admin/overview",
                         "/api/admin-platform/overview"):
                operation = schema["paths"].get(path, {}).get("get")
                if operation is None:
                    continue
                operation["responses"].setdefault("200", {}).setdefault("headers", {}).update({
                    "X-Analytics-Source": {"schema": {"type": "string"}},
                    "X-Analytics-Coverage": {"schema": {"type": "string"}},
                })
                if legacy_read_mode == "analytics":
                    operation["responses"]["503"] = {"description": "Analytics compatibility coverage unavailable.",
                        "headers": {"X-Analytics-Source": {"schema": {"type": "string"}},
                                    "X-Analytics-Coverage": {"schema": {"type": "string"}}}}
            if legacy_read_mode == "analytics":
                for prefix in ("/admin", "/api/admin"):
                    for suffix, method in (("/users/{user_id}/profile", "get"), ("/users/{user_id}/quota", "post")):
                        operation = schema["paths"].get(prefix + suffix, {}).get(method)
                        if operation is not None:
                            operation["responses"].setdefault("200", {}).setdefault("headers", {}).update({
                                name: {"schema": {"type": "string"}} for name in (
                                    "X-Analytics-Source", "X-Analytics-Coverage", "X-Analytics-Coverage-Through-Revision",
                                    "X-Analytics-Source-Snapshot-At", "X-Analytics-Source-HWM", "X-Analytics-Freshness",
                                    "X-Analytics-Browser-Quality", "X-Analytics-Browser-As-Of")})
                            operation["responses"]["503"] = {"description": "Analytics profile coverage unavailable."}
            return schema
        app.openapi = legacy_read_openapi
    mount_frontend(app)

    return app
