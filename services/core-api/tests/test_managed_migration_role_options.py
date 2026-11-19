"""Guard the opt-in ownership contract through the canonical Alembic CLI."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("options, message", [
    ({"KNOWI_MIGRATION_OWNER": "owner"}, "specified together"),
    ({"KNOWI_MIGRATION_APP_ROLE": "app"}, "specified together"),
    ({"KNOWI_MIGRATION_OWNER": "owner", "KNOWI_MIGRATION_APP_ROLE": "owner"}, "must be distinct"),
    ({"KNOWI_MIGRATION_OWNER": "owner", "KNOWI_MIGRATION_APP_ROLE": "migration"}, "must be distinct"),
    ({"KNOWI_MIGRATION_OWNER": "owner", "KNOWI_MIGRATION_APP_ROLE": "app"}, "require an online"),
])
def test_invalid_or_offline_managed_configuration_emits_no_ddl(options, message):
    env = {key: value for key, value in os.environ.items() if not key.startswith("KNOWI_MIGRATION_")}
    env.update(KNOWI_MIGRATION_URL="postgresql+asyncpg://migration:private@postgres.internal/core",
               KNOWI_MIGRATION_HOST="postgres.internal", KNOWI_MIGRATION_DATABASE="core",
               KNOWI_MIGRATION_USER="migration", PYTHONDONTWRITEBYTECODE="1", **options)
    result = subprocess.run([sys.executable, "-m", "alembic", "-c",
                             "services/core-api/alembic-runtime.ini", "upgrade", "head", "--sql"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode != 0
    assert message in result.stderr
    assert "CREATE TABLE" not in result.stdout
    assert "private" not in result.stderr
