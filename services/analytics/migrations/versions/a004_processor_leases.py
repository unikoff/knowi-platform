"""Fenced synthetic processing and durable effect deduplication."""
import re
from alembic import op

revision = "a004"
down_revision = "a003"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    processor, privacy, query = (quote(f"{name}_{role}") for role in ("processor", "privacy", "query"))
    op.execute("ALTER TABLE analytics.processing_jobs ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0)")
    op.execute("ALTER TABLE analytics.processing_jobs ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE analytics.processing_jobs ADD COLUMN claim_id uuid")
    op.execute("ALTER TABLE analytics.processing_jobs ADD COLUMN last_failure text")
    op.execute("CREATE INDEX processing_jobs_claim_candidates ON analytics.processing_jobs (consumer, consumer_version, status, next_attempt_at, subject_ref, created_at)")
    op.execute("""CREATE TABLE analytics.processing_leases (
        consumer varchar(128) NOT NULL, consumer_version integer NOT NULL,
        subject_ref uuid NOT NULL, generation bigint NOT NULL CHECK (generation >= 1),
        owner uuid, expires_at timestamptz NOT NULL, claim_id uuid,
        subject_epoch bigint NOT NULL CHECK (subject_epoch >= 0),
        PRIMARY KEY (consumer, consumer_version, subject_ref)
    )""")
    op.execute("""CREATE TABLE analytics.processing_receipts (
        consumer varchar(128) NOT NULL, consumer_version integer NOT NULL,
        producer_namespace varchar(128) NOT NULL, event_id uuid NOT NULL,
        subject_ref uuid NOT NULL,
        PRIMARY KEY (consumer,consumer_version,producer_namespace,event_id),
        FOREIGN KEY (producer_namespace,event_id)
          REFERENCES analytics.activity_facts(producer_namespace,event_id) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX processing_receipts_subject ON analytics.processing_receipts(subject_ref)")
    op.execute("""CREATE TABLE analytics.processing_results (
        consumer varchar(128) NOT NULL, consumer_version integer NOT NULL,
        subject_ref uuid NOT NULL, subject_epoch bigint NOT NULL,
        courses jsonb NOT NULL, fingerprint_xor char(64) NOT NULL,
        covered_count bigint NOT NULL CHECK (covered_count >= 0),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (consumer,consumer_version,subject_ref)
    )""")
    for table in ("processing_leases", "processing_receipts", "processing_results"):
        verbs = "SELECT, INSERT" if table == "processing_receipts" else "SELECT, INSERT, UPDATE"
        op.execute(f"GRANT {verbs} ON analytics.{table} TO {processor}")
        op.execute(f"GRANT SELECT, DELETE ON analytics.{table} TO {privacy}")
    op.execute(f"GRANT SELECT ON analytics.processing_results TO {query}")
    op.execute(f"GRANT SELECT (consumer, consumer_version, producer_namespace, event_id, subject_ref) ON analytics.processing_receipts TO {query}")


def downgrade():
    raise RuntimeError("a004 is forward-only; preserve leases, receipts and results")
