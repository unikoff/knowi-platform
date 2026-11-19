"""Add course projections without changing the measured activity-v1 sources."""
import re
from alembic import op

revision = "a012"
down_revision = "a011"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    processor, query, privacy, status = (quote(f"{name}_{part}") for part in
                                         ("processor", "query", "privacy", "transfer_status"))
    op.execute("ALTER TABLE analytics.activity_projection_buckets ADD COLUMN course_projection_generation bigint, ADD COLUMN course_projection_run_id uuid")
    op.execute("ALTER TABLE analytics.activity_projection_days ADD COLUMN course_projection_generation bigint, ADD COLUMN course_projection_run_id uuid")
    op.execute("""CREATE TABLE analytics.activity_course_buckets (
        subject_ref uuid NOT NULL, bucket_start timestamptz NOT NULL,
        metric_version text NOT NULL, course_id text NOT NULL,
        generation bigint NOT NULL CHECK(generation>0), subject_epoch bigint NOT NULL CHECK(subject_epoch>=0),
        run_id uuid NOT NULL, winner_intervals jsonb NOT NULL, contributions jsonb NOT NULL,
        theory_ms bigint NOT NULL CHECK(theory_ms>=0), practice_ms bigint NOT NULL CHECK(practice_ms>=0),
        total_ms bigint NOT NULL CHECK(total_ms>0 AND total_ms=theory_ms+practice_ms),
        PRIMARY KEY(subject_ref,bucket_start,metric_version,course_id),
        FOREIGN KEY(subject_ref,bucket_start,metric_version) REFERENCES analytics.activity_projection_buckets
          (subject_ref,bucket_start,metric_version) ON DELETE CASCADE,
        CHECK(jsonb_typeof(winner_intervals)='array' AND jsonb_array_length(winner_intervals)>0),
        CHECK(jsonb_typeof(contributions)='array' AND jsonb_array_length(contributions)>0))""")
    op.execute("""CREATE TABLE analytics.activity_course_days (
        subject_ref uuid NOT NULL, local_day date NOT NULL, metric_version text NOT NULL, course_id text NOT NULL,
        generation bigint NOT NULL CHECK(generation>0), subject_epoch bigint NOT NULL CHECK(subject_epoch>=0),
        run_id uuid NOT NULL, theory_ms bigint NOT NULL CHECK(theory_ms>=0),
        practice_ms bigint NOT NULL CHECK(practice_ms>=0), total_ms bigint NOT NULL CHECK(total_ms>0),
        PRIMARY KEY(subject_ref,local_day,metric_version,course_id), CHECK(total_ms=theory_ms+practice_ms),
        FOREIGN KEY(subject_ref,local_day,metric_version) REFERENCES analytics.activity_projection_days
          (subject_ref,local_day,metric_version) ON DELETE CASCADE)""")
    op.execute("""CREATE FUNCTION analytics.publish_activity_course_bucket() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
        DECLARE current_run uuid;
        BEGIN
          IF NEW.state<>'ready' THEN RETURN NEW; END IF;
          SELECT run_id INTO current_run FROM analytics.activity_projection_backfill WHERE singleton=true;
          IF current_run IS NULL THEN
            NEW.course_projection_generation := NULL;
            NEW.course_projection_run_id := NULL;
            RETURN NEW;
          END IF;
          DELETE FROM analytics.activity_course_buckets WHERE subject_ref=NEW.subject_ref
            AND bucket_start=NEW.bucket_start AND metric_version=NEW.metric_version;
          INSERT INTO analytics.activity_course_buckets
            (subject_ref,bucket_start,metric_version,course_id,generation,subject_epoch,run_id,winner_intervals,contributions,theory_ms,practice_ms,total_ms)
          SELECT NEW.subject_ref,NEW.bucket_start,NEW.metric_version,c.course_id,NEW.generation,NEW.subject_epoch,current_run,
            (SELECT jsonb_agg(w.value ORDER BY w.ordinality) FROM jsonb_array_elements(NEW.winner_intervals)
               WITH ORDINALITY w(value,ordinality) WHERE w.value->>'course_id'=c.course_id),
            jsonb_agg(c.value ORDER BY c.ordinality),
            sum(CASE WHEN c.value->>'section'='theory' THEN (c.value->>'active_ms')::bigint ELSE 0 END),
            sum(CASE WHEN c.value->>'section'='practice' THEN (c.value->>'active_ms')::bigint ELSE 0 END),
            sum((c.value->>'active_ms')::bigint)
          FROM (SELECT value,ordinality,value->>'course_id' AS course_id FROM jsonb_array_elements(NEW.contributions)
                WITH ORDINALITY) c GROUP BY c.course_id;
          NEW.course_projection_generation := NEW.generation;
          NEW.course_projection_run_id := current_run;
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.publish_activity_course_bucket() FROM PUBLIC")
    op.execute("""CREATE TRIGGER publish_activity_course_bucket BEFORE UPDATE ON analytics.activity_projection_buckets
        FOR EACH ROW WHEN (NEW.state='ready') EXECUTE FUNCTION analytics.publish_activity_course_bucket()""")
    op.execute("""CREATE FUNCTION analytics.mark_activity_course_day() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,analytics AS $$
        BEGIN
          NEW.course_projection_generation := NEW.generation;
          NEW.course_projection_run_id := NEW.run_id;
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.mark_activity_course_day() FROM PUBLIC")
    op.execute("""CREATE TRIGGER mark_activity_course_day BEFORE INSERT OR UPDATE ON analytics.activity_projection_days
        FOR EACH ROW EXECUTE FUNCTION analytics.mark_activity_course_day()""")
    op.execute("""CREATE FUNCTION analytics.publish_activity_course_day() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
        BEGIN
          DELETE FROM analytics.activity_course_days WHERE subject_ref=NEW.subject_ref
            AND local_day=NEW.local_day AND metric_version=NEW.metric_version;
          INSERT INTO analytics.activity_course_days
            (subject_ref,local_day,metric_version,course_id,generation,subject_epoch,run_id,theory_ms,practice_ms,total_ms)
          SELECT NEW.subject_ref,NEW.local_day,NEW.metric_version,c.course_id,NEW.generation,NEW.subject_epoch,NEW.run_id,
            sum(CASE WHEN c.section='theory' THEN c.active_ms ELSE 0 END),
            sum(CASE WHEN c.section='practice' THEN c.active_ms ELSE 0 END),sum(c.active_ms)
          FROM (SELECT value->>'course_id' AS course_id,value->>'section' AS section,
                       (value->>'active_ms')::bigint AS active_ms FROM jsonb_array_elements(NEW.contributions)) c
          GROUP BY c.course_id HAVING sum(c.active_ms)>0;
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.publish_activity_course_day() FROM PUBLIC")
    op.execute("""CREATE TRIGGER publish_activity_course_day AFTER INSERT OR UPDATE ON analytics.activity_projection_days
        FOR EACH ROW EXECUTE FUNCTION analytics.publish_activity_course_day()""")
    op.execute(f"GRANT SELECT ON analytics.activity_course_buckets,analytics.activity_course_days TO {processor},{query}")
    op.execute(f"GRANT SELECT,DELETE ON analytics.activity_course_buckets,analytics.activity_course_days TO {privacy}")
    op.execute(f"GRANT SELECT (subject_ref) ON analytics.activity_course_buckets,analytics.activity_course_days TO {status}")


def downgrade():
    op.execute("DROP TRIGGER publish_activity_course_day ON analytics.activity_projection_days")
    op.execute("DROP TRIGGER mark_activity_course_day ON analytics.activity_projection_days")
    op.execute("DROP TRIGGER publish_activity_course_bucket ON analytics.activity_projection_buckets")
    op.execute("DROP FUNCTION analytics.publish_activity_course_day()")
    op.execute("DROP FUNCTION analytics.mark_activity_course_day()")
    op.execute("DROP FUNCTION analytics.publish_activity_course_bucket()")
    op.execute("DROP TABLE analytics.activity_course_days")
    op.execute("DROP TABLE analytics.activity_course_buckets")
    op.execute("ALTER TABLE analytics.activity_projection_days DROP COLUMN course_projection_generation, DROP COLUMN course_projection_run_id")
    op.execute("ALTER TABLE analytics.activity_projection_buckets DROP COLUMN course_projection_generation, DROP COLUMN course_projection_run_id")
