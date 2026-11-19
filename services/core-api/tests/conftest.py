"""Disposable PostgreSQL engine for Core source-retirement regression fixtures."""
import importlib.util
import os
from pathlib import Path
import re
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


@pytest.fixture
def synthetic_engine():
    raw = os.environ.get('KNOWI_TEST_PG_URL')
    if not raw:
        pytest.fail('KNOWI_TEST_PG_URL required for disposable PostgreSQL rehearsal; no fallback')
    path = Path(__file__).parent / 'integration' / 'test_course_progress_release.py'
    spec = importlib.util.spec_from_file_location('course_progress_fixture_guard', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    base = helper._allowlisted_url(raw)
    name = 'c527_role_ci_' + uuid4().hex
    assert re.fullmatch(r'c527_role_ci_[0-9a-f]{32}', name)
    with psycopg.connect(base.set(drivername='postgresql').render_as_string(hide_password=False), autocommit=True) as admin:
        db, user, version = admin.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int").fetchone()
        assert (db, user) == ('pymentor_test', 'pymentor_test') and 160000 <= version < 170000
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        engine = None
        try:
            engine = create_engine(base.set(drivername='postgresql+psycopg', database=name), poolclass=NullPool)
            yield engine
        finally:
            if engine is not None:
                engine.dispose()
            assert re.fullmatch(r'c527_role_ci_[0-9a-f]{32}', name)
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
