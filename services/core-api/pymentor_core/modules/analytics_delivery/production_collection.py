"""Private deletion snapshot process, intended behind a loopback mTLS proxy.

Run this ASGI app separately from Core's public server. The proxy must verify
client certificates and be the only network listener exposing this process.
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import os
from pathlib import Path
import re

from fastapi import FastAPI
from sqlalchemy.engine import make_url
from starlette.concurrency import run_in_threadpool

from pymentor_core.modules.analytics_delivery.local_collection import LocalCollection, _uuid, read_snapshot, router


def configure(env):
    if env.get("PYMENTOR_ANALYTICS_PRIVATE_SOURCE_ENABLED") != "1":
        raise ValueError("private Analytics source disabled")
    try:
        url = make_url(env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_DATABASE_URL"])
        if (url.drivername != "postgresql+psycopg" or not url.password
                or url.host != env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_HOST"]
                or url.database != env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_DATABASE"]
                or url.username != env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_EXPECTED_USER"]
                or not all((url.host, url.database, url.username))
                or set(url.query) != {"sslmode", "sslrootcert"}
                or url.query["sslmode"] != "verify-full" or not url.query["sslrootcert"]):
            raise ValueError
        encoded = Path(env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_KEY_FILE"]).read_bytes()
        if not re.fullmatch(rb"[A-Za-z0-9_-]{43}", encoded):
            raise ValueError
        key = base64.urlsafe_b64decode(encoded + b"=")
        if len(key) != 32 or base64.urlsafe_b64encode(key).rstrip(b"=") != encoded:
            raise ValueError
        return LocalCollection(_uuid(env["PYMENTOR_ANALYTICS_PRIVATE_SOURCE_INSTANCE_ID"]),
                               url.database, str(url.render_as_string(hide_password=False)), key,
                               expected_user=url.username)
    except Exception:
        raise ValueError("invalid private Analytics source configuration") from None


def create_app():
    config = configure(os.environ)

    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(read_snapshot, config)
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.analytics_local_collection = config
    app.include_router(router, prefix="/api")
    return app
