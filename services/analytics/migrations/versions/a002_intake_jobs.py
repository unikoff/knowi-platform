"""Transactional synthetic fan-out, preserving a001 facts.

Revision ID: a002
"""
import re
from alembic import op

revision = "a002"
down_revision = "a001"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    collector, processor, privacy = (quote(f"{name}_{role}") for role in ("collector", "processor", "privacy"))
    op.execute("""CREATE TABLE analytics.processing_jobs (
        producer_namespace varchar(128) NOT NULL,
        event_id uuid NOT NULL,
        consumer varchar(128) NOT NULL,
        consumer_version integer NOT NULL CHECK (consumer_version > 0),
        subject_ref uuid NOT NULL,
        status text NOT NULL DEFAULT 'pending',
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (producer_namespace, event_id, consumer, consumer_version),
        FOREIGN KEY (producer_namespace, event_id)
          REFERENCES analytics.activity_facts (producer_namespace, event_id) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX processing_jobs_consumer_subject ON analytics.processing_jobs (consumer, consumer_version, status, subject_ref, created_at)")
    # Historical a001 synthetic facts acquire the initial subscriptions atomically.
    # Keep this frozen rather than importing a mutable runtime registry.
    op.execute("""INSERT INTO analytics.processing_jobs
        (producer_namespace, event_id, consumer, consumer_version, subject_ref)
        SELECT f.producer_namespace, f.event_id, subscriptions.consumer, 1, f.subject_ref
        FROM analytics.activity_facts f
        CROSS JOIN (VALUES ('synthetic.activity'), ('synthetic.independent')) AS subscriptions(consumer)
        WHERE NOT EXISTS (SELECT 1 FROM analytics.subject_fences s
                          WHERE s.subject_ref=f.subject_ref AND s.deleted)
    """)
    op.execute(f"GRANT SELECT, INSERT ON analytics.processing_jobs TO {collector}")
    op.execute(f"GRANT SELECT, UPDATE ON analytics.processing_jobs TO {processor}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.processing_jobs TO {privacy}")


def downgrade():
    raise RuntimeError("a002 is forward-only; application rollback must preserve facts and jobs")
