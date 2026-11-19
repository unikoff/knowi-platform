"""Separately versioned, invalidatable boundary-credit projections."""
import re
from alembic import op

revision = "a013"
down_revision = "a012"
branch_labels = depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    processor, query, privacy, status = (quote(f"{name}_{part}") for part in
                                         ("processor", "query", "privacy", "transfer_status"))
    op.execute("""CREATE TABLE analytics.activity_credit_windows (
        subject_ref uuid NOT NULL, bucket_start timestamptz NOT NULL, metric_version text NOT NULL,
        credit_version text NOT NULL CHECK(credit_version='boundary-v1'),
        generation bigint NOT NULL CHECK(generation>0), state text NOT NULL CHECK(state IN ('dirty','ready')),
        subject_epoch bigint, run_id uuid, credits jsonb,
        PRIMARY KEY(subject_ref,bucket_start,metric_version,credit_version),
        FOREIGN KEY(subject_ref,bucket_start,metric_version) REFERENCES analytics.activity_projection_buckets
          (subject_ref,bucket_start,metric_version) ON DELETE CASCADE,
        CHECK(state<>'ready' OR (subject_epoch IS NOT NULL AND run_id IS NOT NULL AND credits IS NOT NULL AND jsonb_typeof(credits)='array')))
    """)
    op.execute("CREATE INDEX activity_credit_window_work ON analytics.activity_credit_windows(subject_ref,bucket_start) WHERE state='dirty'")
    op.execute("""CREATE TABLE analytics.activity_credit_days (
        subject_ref uuid NOT NULL, local_day date NOT NULL, metric_version text NOT NULL,
        credit_version text NOT NULL CHECK(credit_version='boundary-v1'),
        generation bigint NOT NULL CHECK(generation>0), state text NOT NULL CHECK(state IN ('dirty','ready')),
        subject_epoch bigint, run_id uuid, credits jsonb,
        PRIMARY KEY(subject_ref,local_day,metric_version,credit_version),
        FOREIGN KEY(subject_ref,local_day,metric_version) REFERENCES analytics.activity_projection_day_sources
          (subject_ref,local_day,metric_version) ON DELETE CASCADE,
        CHECK(state<>'ready' OR (subject_epoch IS NOT NULL AND run_id IS NOT NULL AND credits IS NOT NULL AND jsonb_typeof(credits)='array')))
    """)
    op.execute("CREATE INDEX activity_credit_day_work ON analytics.activity_credit_days(subject_ref,local_day) WHERE state='dirty'")
    op.execute(r"""CREATE FUNCTION analytics.invalidate_activity_credit() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
      DECLARE subj uuid; source_time timestamptz; metric text;
      BEGIN
        IF TG_OP='UPDATE' AND OLD.generation IS NOT DISTINCT FROM NEW.generation
          AND (OLD.state='ready')=(NEW.state='ready') AND
          (OLD.projection_generation,OLD.subject_epoch,OLD.winner_intervals) IS NOT DISTINCT FROM
          (NEW.projection_generation,NEW.subject_epoch,NEW.winner_intervals) THEN RETURN NEW; END IF;
        IF TG_OP='DELETE' THEN subj:=OLD.subject_ref; source_time:=OLD.bucket_start; metric:=OLD.metric_version;
        ELSE subj:=NEW.subject_ref; source_time:=NEW.bucket_start; metric:=NEW.metric_version; END IF;
        IF EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=subj AND deleted) THEN
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END IF;
        INSERT INTO analytics.activity_credit_windows(subject_ref,bucket_start,metric_version,credit_version,generation,state)
        SELECT b.subject_ref,b.bucket_start,b.metric_version,'boundary-v1',1,'dirty'
        FROM analytics.activity_projection_buckets b WHERE b.subject_ref=subj AND b.metric_version=metric
          AND b.bucket_start BETWEEN source_time-interval '15 minutes' AND source_time+interval '30 minutes'
        ON CONFLICT(subject_ref,bucket_start,metric_version,credit_version) DO UPDATE
          SET generation=analytics.activity_credit_windows.generation+1,state='dirty';
        INSERT INTO analytics.activity_credit_days(subject_ref,local_day,metric_version,credit_version,generation,state)
        SELECT DISTINCT s.subject_ref,s.local_day,s.metric_version,'boundary-v1',1,'dirty'
        FROM analytics.activity_projection_day_sources s
        WHERE s.subject_ref=subj AND s.metric_version=metric AND s.local_day BETWEEN
          ((source_time-interval '15 minutes') AT TIME ZONE 'UTC' + interval '3 hours')::date AND
          ((source_time+interval '30 minutes') AT TIME ZONE 'UTC' + interval '3 hours')::date
        ON CONFLICT(subject_ref,local_day,metric_version,credit_version) DO UPDATE
          SET generation=analytics.activity_credit_days.generation+1,state='dirty';
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.invalidate_activity_credit() FROM PUBLIC")
    op.execute("""CREATE TRIGGER credit_bucket_changed AFTER INSERT OR UPDATE OR DELETE
      ON analytics.activity_projection_buckets FOR EACH ROW EXECUTE FUNCTION analytics.invalidate_activity_credit()""")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON analytics.activity_credit_windows,analytics.activity_credit_days TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.activity_credit_windows,analytics.activity_credit_days TO {query}")
    op.execute(f"GRANT SELECT,DELETE ON analytics.activity_credit_windows,analytics.activity_credit_days TO {privacy}")
    op.execute(f"GRANT SELECT(subject_ref) ON analytics.activity_credit_windows,analytics.activity_credit_days TO {status}")


def downgrade():
    op.execute("DROP TRIGGER credit_bucket_changed ON analytics.activity_projection_buckets")
    op.execute("DROP FUNCTION analytics.invalidate_activity_credit()")
    op.execute("DROP TABLE analytics.activity_credit_days")
    op.execute("DROP TABLE analytics.activity_credit_windows")
