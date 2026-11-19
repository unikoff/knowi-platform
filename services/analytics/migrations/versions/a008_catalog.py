"""PROPOSED ONLY. Apply only after the a007 and infrastructure gates."""
import re
from alembic import op

revision = "a008"
down_revision = "a007"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    collector = conn.dialect.identifier_preparer.quote(f"{name}_collector")
    op.execute("""CREATE TABLE analytics.catalog_versions (
        producer_namespace varchar(128) NOT NULL, course_id varchar(128) NOT NULL,
        catalog_version varchar(128) NOT NULL, sequence bigint NOT NULL CHECK (sequence >= 1),
        fingerprint char(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        body jsonb NOT NULL, committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (producer_namespace,course_id,catalog_version),
        UNIQUE (producer_namespace,course_id,sequence))""")
    op.execute(f"GRANT SELECT, INSERT ON analytics.catalog_versions TO {collector}")


def downgrade():
    raise RuntimeError("catalog history is forward-only")
