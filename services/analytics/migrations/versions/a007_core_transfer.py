"""PROPOSED ONLY: common ledger Core expansion. Not an active revision.

Provision the NOINHERIT, read-only <database>_transfer_status role before
applying. Copy to Alembic versions only after the external a006 gates pass.
"""
import re
from alembic import op

revision = "a007"
down_revision = "a006"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    status_name = f"{name}_transfer_status"
    if len(status_name) > 63:
        raise ValueError("transfer status role name exceeds PostgreSQL limit")
    role = conn.exec_driver_sql("""SELECT oid,rolsuper,rolinherit,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin
        FROM pg_roles WHERE rolname=%s""", (status_name,)).one_or_none()
    if (role is None or any(role[1:6]) or not role.rolcanlogin or conn.exec_driver_sql(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=%s)", (role.oid,)).scalar_one()):
        raise RuntimeError("dedicated unprivileged NOINHERIT transfer status role must be provisioned")
    quote = conn.dialect.identifier_preparer.quote
    collector, processor, query, privacy, status = (quote(f"{name}_{part}") for part in
                                                     ("collector", "processor", "query", "privacy", "transfer_status"))
    op.execute("ALTER TABLE analytics.activity_facts ADD COLUMN source_kind text NOT NULL DEFAULT 'browser_v2'")
    for column in ("course_id", "catalog_version", "started_at"):
        op.execute(f"ALTER TABLE analytics.activity_facts ALTER COLUMN {column} DROP NOT NULL")
    op.execute("""ALTER TABLE analytics.activity_facts ADD CONSTRAINT activity_fact_source_shape CHECK (
        (source_kind='browser_v2' AND course_id IS NOT NULL AND catalog_version IS NOT NULL AND started_at IS NOT NULL)
        OR (source_kind='core_transfer_v1' AND course_id IS NULL AND catalog_version IS NULL AND lesson_id IS NULL AND started_at IS NULL))""")
    op.execute("CREATE INDEX activity_browser_time_subject ON analytics.activity_facts (started_at,subject_ref) WHERE source_kind='browser_v2'")
    op.execute("""CREATE TABLE analytics.core_transfer_entities (
        producer_namespace varchar(128) NOT NULL, event_id uuid NOT NULL, subject_ref uuid NOT NULL,
        entity_key char(64) NOT NULL CHECK (entity_key ~ '^[0-9a-f]{64}$'),
        revision bigint NOT NULL CHECK (revision >= 0),
        canonical_hash char(64) NOT NULL CHECK (canonical_hash ~ '^[0-9a-f]{64}$'),
        PRIMARY KEY (producer_namespace,subject_ref,entity_key,revision),
        UNIQUE (producer_namespace,event_id,subject_ref),
        UNIQUE (producer_namespace,subject_ref,entity_key,revision,event_id),
        FOREIGN KEY (producer_namespace,event_id,subject_ref)
          REFERENCES analytics.activity_facts(producer_namespace,event_id,subject_ref) ON DELETE CASCADE)""")
    op.execute("CREATE INDEX core_transfer_entities_subject ON analytics.core_transfer_entities(subject_ref)")
    op.execute("""CREATE TABLE analytics.core_transfer_projection (
        producer_namespace varchar(128) NOT NULL, subject_ref uuid NOT NULL,
        entity_key char(64) NOT NULL CHECK (entity_key ~ '^[0-9a-f]{64}$'),
        revision bigint NOT NULL CHECK (revision >= 0), event_id uuid NOT NULL,
        PRIMARY KEY (producer_namespace,subject_ref,entity_key),
        FOREIGN KEY (producer_namespace,event_id,subject_ref)
          REFERENCES analytics.activity_facts(producer_namespace,event_id,subject_ref) ON DELETE CASCADE,
        FOREIGN KEY (producer_namespace,subject_ref,entity_key,revision,event_id)
          REFERENCES analytics.core_transfer_entities(producer_namespace,subject_ref,entity_key,revision,event_id) ON DELETE CASCADE)""")
    op.execute("CREATE INDEX core_transfer_projection_subject ON analytics.core_transfer_projection(subject_ref)")
    op.execute(f"GRANT SELECT, INSERT ON analytics.core_transfer_entities TO {collector}")
    op.execute(f"GRANT SELECT (consumer,consumer_version,producer_namespace,event_id,subject_ref) ON analytics.processing_receipts TO {collector}")
    op.execute(f"GRANT SELECT ON analytics.core_transfer_entities TO {processor}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON analytics.core_transfer_projection TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.core_transfer_projection TO {query}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.core_transfer_entities, analytics.core_transfer_projection TO {privacy}")
    op.execute(f"GRANT USAGE ON SCHEMA analytics TO {status}")
    for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results",
                  "processing_leases", "processing_replays", "admission_subject_windows",
                  "core_transfer_entities", "core_transfer_projection"):
        op.execute(f"GRANT SELECT (subject_ref) ON analytics.{table} TO {status}")
    op.execute(f"GRANT SELECT (subject_ref,epoch,deleted) ON analytics.subject_fences TO {status}")
    op.execute(f"GRANT SELECT (singleton,authority_id,applied_through) ON analytics.privacy_state TO {status}")
    op.execute(f"GRANT SELECT (seq,control_id,payload_hash,subject_ref,fence_epoch,phase) ON analytics.privacy_receipts TO {status}")


def downgrade():
    raise RuntimeError("a007 is forward-only; preserve Core facts, identity fences and projections")
