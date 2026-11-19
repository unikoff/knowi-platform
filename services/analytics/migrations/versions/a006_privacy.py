"""Applied journal prefix and resumable privacy deletion receipts."""
import re
from alembic import op

revision = "a006"
down_revision = "a005"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    privacy, collector, processor, query = (quote(f"{name}_{role}") for role in ("privacy", "collector", "processor", "query"))
    op.execute("""CREATE TABLE analytics.privacy_state (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        authority_id varchar(128) NOT NULL CHECK (authority_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        applied_through bigint NOT NULL DEFAULT 0 CHECK (applied_through >= 0)
    )""")
    op.execute("""CREATE TABLE analytics.privacy_receipts (
        seq bigint PRIMARY KEY CHECK (seq >= 1),
        control_id uuid NOT NULL UNIQUE,
        payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
        subject_ref uuid NOT NULL,
        fence_epoch bigint NOT NULL CHECK (fence_epoch >= 1),
        phase text NOT NULL CHECK (phase IN ('purging','complete'))
    )""")
    op.execute("""CREATE FUNCTION analytics.protect_subject_tombstone() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, analytics AS $$
        BEGIN
          IF NEW.subject_ref IS DISTINCT FROM OLD.subject_ref OR NEW.epoch < OLD.epoch
             OR (OLD.deleted AND NOT NEW.deleted) THEN
            RAISE EXCEPTION 'subject fence cannot be reversed';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER subject_fence_monotonic BEFORE UPDATE ON analytics.subject_fences
        FOR EACH ROW EXECUTE FUNCTION analytics.protect_subject_tombstone()""")
    op.execute("CREATE INDEX privacy_jobs_subject ON analytics.processing_jobs(subject_ref)")
    op.execute("CREATE INDEX privacy_results_subject ON analytics.processing_results(subject_ref)")
    op.execute("CREATE INDEX privacy_leases_subject ON analytics.processing_leases(subject_ref)")
    op.execute("CREATE INDEX privacy_replays_subject ON analytics.processing_replays(subject_ref)")
    op.execute(f"GRANT SELECT, INSERT ON analytics.privacy_state, analytics.privacy_receipts TO {privacy}")
    op.execute(f"GRANT UPDATE (applied_through) ON analytics.privacy_state TO {privacy}")
    op.execute(f"GRANT UPDATE (phase) ON analytics.privacy_receipts TO {privacy}")
    for role in (collector, processor, query):
        op.execute(f"GRANT SELECT (singleton,authority_id,applied_through) ON analytics.privacy_state TO {role}")


def downgrade():
    raise RuntimeError("a006 is forward-only; preserve applied journal prefix and deletion receipts")
