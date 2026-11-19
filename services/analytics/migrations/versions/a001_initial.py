"""Initial Analytics-only schema. Forward-only: preserve stored facts on rollback.

Revision ID: a001
"""
import re
from alembic import op

revision = "a001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    roles = {part: quote(f"{name}_{part}") for part in ("collector", "processor", "query", "privacy")}
    op.execute("CREATE SCHEMA analytics")
    op.execute("""CREATE TABLE analytics.activity_facts (
        producer_namespace varchar(128) NOT NULL,
        event_id uuid NOT NULL,
        subject_ref uuid NOT NULL,
        course_id varchar(128) NOT NULL,
        catalog_version varchar(128) NOT NULL,
        lesson_id varchar(128),
        started_at timestamptz NOT NULL,
        received_at timestamptz NOT NULL,
        canonical_hash char(64) NOT NULL CHECK (canonical_hash ~ '^[0-9a-f]{64}$'),
        hash_version smallint NOT NULL DEFAULT 1 CHECK (hash_version = 1),
        accepted_event jsonb NOT NULL,
        PRIMARY KEY (producer_namespace, event_id)
    )""")
    op.execute("CREATE INDEX activity_course_lesson_catalog_time ON analytics.activity_facts (course_id, lesson_id, catalog_version, started_at)")
    op.execute("CREATE INDEX activity_subject_time ON analytics.activity_facts (subject_ref, started_at)")
    op.execute("""CREATE TABLE analytics.subject_fences (
        subject_ref uuid PRIMARY KEY, epoch bigint NOT NULL CHECK (epoch >= 1),
        deleted boolean NOT NULL, control_id uuid NOT NULL UNIQUE
    )""")
    op.execute("REVOKE ALL ON SCHEMA analytics FROM PUBLIC")
    for role in roles.values():
        op.execute(f"GRANT USAGE ON SCHEMA analytics TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON analytics.activity_facts TO {roles['collector']}")
    op.execute(f"GRANT SELECT ON analytics.activity_facts TO {roles['processor']}, {roles['query']}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.activity_facts TO {roles['privacy']}")
    op.execute(f"GRANT SELECT ON analytics.subject_fences TO {roles['collector']}, {roles['processor']}, {roles['query']}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON analytics.subject_fences TO {roles['privacy']}")


def downgrade():
    raise RuntimeError("a001 is forward-only; application rollback must preserve Analytics data")
