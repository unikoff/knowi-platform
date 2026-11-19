"""Common subject/day activity projection; forward only."""
import re
from alembic import op
from sqlalchemy import text

revision = "a011"
down_revision = "a010"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name) or len(name + "_transfer_status") > 63:
        raise ValueError("unsafe Analytics database name")
    identity = conn.execute(text("SELECT current_user,rolcanlogin,rolcreaterole,rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user")).one()
    if identity[0] != name + "_migrate" or not identity[1] or identity[2] or identity[3]:
        raise ValueError("limited Analytics migration owner required")
    public_create = conn.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n,
        LATERAL pg_catalog.aclexplode(coalesce(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
        WHERE n.nspname IN ('pg_catalog','analytics') AND a.grantee=0 AND a.privilege_type='CREATE')""")).scalar_one()
    if public_create:
        raise ValueError("untrusted public CREATE on function search path")
    for part in ("collector", "processor", "query", "privacy", "transfer_status"):
        role = name + "_" + part
        unsafe = conn.execute(text("""SELECT pg_catalog.has_schema_privilege(:role,'analytics','CREATE') OR
            pg_catalog.has_schema_privilege(:role,'pg_catalog','CREATE') OR pg_catalog.pg_has_role(:role,:owner,'MEMBER')"""),
            {"role": role, "owner": identity[0]}).scalar_one()
        if unsafe:
            raise ValueError("untrusted role can modify function search path or assume its owner")
    quote = conn.dialect.identifier_preparer.quote
    processor, query, privacy, status = (quote(f"{name}_{part}") for part in ("processor", "query", "privacy", "transfer_status"))
    op.execute("""CREATE TABLE analytics.activity_projection_day_sources (
        subject_ref uuid NOT NULL, local_day date NOT NULL,
        metric_version text NOT NULL DEFAULT 'activity-v1' CHECK(metric_version='activity-v1'),
        generation bigint NOT NULL CHECK(generation>=1),
        state text NOT NULL CHECK(state IN ('dirty','claimed','ready','quarantined')),
        claim_id uuid, owner uuid, lease_expires_at timestamptz, claim_generation bigint, claim_epoch bigint, claim_run_id uuid,
        attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), next_attempt_at timestamptz NOT NULL DEFAULT now(), last_failure text,
        PRIMARY KEY(subject_ref,local_day,metric_version),
        CHECK(state<>'claimed' OR (claim_id IS NOT NULL AND owner IS NOT NULL AND lease_expires_at IS NOT NULL
            AND claim_generation IS NOT NULL AND claim_run_id IS NOT NULL)))""")
    op.execute("CREATE INDEX activity_projection_day_discovery ON analytics.activity_projection_day_sources(local_day,subject_ref,metric_version)")
    op.execute("CREATE INDEX activity_projection_day_work ON analytics.activity_projection_day_sources(next_attempt_at,local_day,subject_ref) WHERE state IN ('dirty','claimed')")
    op.execute("CREATE INDEX activity_projection_day_outstanding ON analytics.activity_projection_day_sources(state) WHERE state<>'ready'")
    op.execute("""CREATE TABLE analytics.activity_projection_days (
        subject_ref uuid NOT NULL, local_day date NOT NULL,
        metric_version text NOT NULL CHECK(metric_version='activity-v1'), generation bigint NOT NULL CHECK(generation>=1),
        subject_epoch bigint NOT NULL CHECK(subject_epoch>=0), run_id uuid NOT NULL,
        contributions jsonb NOT NULL, presence_labels jsonb NOT NULL,
        source_bucket_count bigint NOT NULL CHECK(source_bucket_count BETWEEN 0 AND 96),
        source_compact_items bigint NOT NULL CHECK(source_compact_items BETWEEN 0 AND 1000000),
        source_compact_bytes bigint NOT NULL CHECK(source_compact_bytes BETWEEN 0 AND 67108864),
        PRIMARY KEY(subject_ref,local_day,metric_version),
        CHECK(CASE WHEN jsonb_typeof(contributions)='array' AND jsonb_typeof(presence_labels)='array' THEN
          jsonb_array_length(contributions)+jsonb_array_length(presence_labels)<=1000000 AND
          octet_length(contributions::text)::bigint+octet_length(presence_labels::text)::bigint<=67108864 ELSE false END))""")
    op.execute("""CREATE TABLE analytics.activity_projection_day_backfill (
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), run_id uuid,
        phase text NOT NULL CHECK(phase IN ('scanning','draining','complete')),
        cursor_subject uuid, cursor_bucket timestamptz, cursor_metric text,
        CHECK((cursor_subject IS NULL)=(cursor_bucket IS NULL) AND (cursor_subject IS NULL)=(cursor_metric IS NULL)))""")
    op.execute("INSERT INTO analytics.activity_projection_day_backfill(singleton,phase) VALUES(true,'scanning')")
    op.execute(r"""CREATE FUNCTION analytics.activity_projection_bucket_changed() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
        DECLARE subject uuid; day_key date; metric text; deleted boolean;
        BEGIN
          IF TG_OP='UPDATE' AND (OLD.subject_ref,OLD.bucket_start,OLD.metric_version)
                IS DISTINCT FROM (NEW.subject_ref,NEW.bucket_start,NEW.metric_version) THEN
            RAISE EXCEPTION 'bucket keys are immutable';
          END IF;
          IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM analytics.subject_fences f WHERE f.subject_ref=NEW.subject_ref AND f.deleted) THEN
            RAISE EXCEPTION 'activity subject is deleted';
          END IF;
          IF TG_OP='UPDATE' AND OLD.generation IS NOT DISTINCT FROM NEW.generation
             AND (OLD.state='ready')=(NEW.state='ready')
             AND (OLD.projection_generation,OLD.subject_epoch,OLD.winner_intervals,OLD.presence_intervals,
                  OLD.presence_points,OLD.contributions,OLD.presence_labels,OLD.has_presence,
                  OLD.compact_item_count,OLD.edge_item_count)
                 IS NOT DISTINCT FROM
                 (NEW.projection_generation,NEW.subject_epoch,NEW.winner_intervals,NEW.presence_intervals,
                  NEW.presence_points,NEW.contributions,NEW.presence_labels,NEW.has_presence,
                  NEW.compact_item_count,NEW.edge_item_count) THEN RETURN NEW; END IF;
          IF TG_OP='DELETE' THEN subject:=OLD.subject_ref; day_key:=(OLD.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date; metric:=OLD.metric_version;
          ELSE subject:=NEW.subject_ref; day_key:=(NEW.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date; metric:=NEW.metric_version; END IF;
          SELECT f.deleted INTO deleted FROM analytics.subject_fences f WHERE f.subject_ref=subject;
          IF coalesce(deleted,false) THEN
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RAISE EXCEPTION 'activity subject is deleted';
          END IF;
          INSERT INTO analytics.activity_projection_day_sources(subject_ref,local_day,metric_version,generation,state)
            VALUES(subject,day_key,metric,1,'dirty') ON CONFLICT(subject_ref,local_day,metric_version) DO UPDATE SET
            generation=analytics.activity_projection_day_sources.generation+1,state='dirty',claim_id=NULL,owner=NULL,
            lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL,attempts=0,
            next_attempt_at=clock_timestamp(),last_failure=NULL;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.activity_projection_bucket_changed() FROM PUBLIC")
    op.execute("""CREATE TRIGGER activity_projection_bucket_changed AFTER INSERT OR UPDATE OR DELETE
        ON analytics.activity_projection_buckets FOR EACH ROW EXECUTE FUNCTION analytics.activity_projection_bucket_changed()""")
    op.execute(r"""CREATE FUNCTION analytics.register_activity_day(p_subject uuid,p_bucket timestamptz,p_metric text,p_run uuid)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
      DECLARE d date;
      BEGIN
        IF p_subject IS NULL OR p_bucket IS NULL OR p_metric<>'activity-v1' OR p_run IS NULL THEN RAISE EXCEPTION 'invalid day registration'; END IF;
        IF NOT analytics.lock_activity_projection_run(p_run) THEN RAISE EXCEPTION 'activity run unavailable'; END IF;
        IF EXISTS(SELECT 1 FROM analytics.subject_fences f WHERE f.subject_ref=p_subject AND f.deleted) THEN RETURN; END IF;
        IF NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets b WHERE b.subject_ref=p_subject AND b.bucket_start=p_bucket AND b.metric_version=p_metric) THEN RETURN; END IF;
        d := (p_bucket AT TIME ZONE 'UTC' + interval '3 hours')::date;
        INSERT INTO analytics.activity_projection_day_sources(subject_ref,local_day,metric_version,generation,state)
          VALUES(p_subject,d,p_metric,1,'dirty') ON CONFLICT DO NOTHING;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.register_activity_day(uuid,timestamptz,text,uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.register_activity_day(uuid,timestamptz,text,uuid) TO {processor}")
    op.execute(r"""CREATE FUNCTION analytics.begin_activity_day_backfill(p_run uuid) RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
      BEGIN
        IF NOT analytics.lock_activity_projection_run(p_run) THEN RAISE EXCEPTION 'activity run unavailable'; END IF;
        UPDATE analytics.activity_projection_day_backfill SET run_id=p_run,phase='scanning',
          cursor_subject=NULL,cursor_bucket=NULL,cursor_metric=NULL WHERE singleton=true AND run_id IS DISTINCT FROM p_run;
        IF NOT FOUND AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_backfill WHERE singleton=true AND run_id=p_run) THEN
          RAISE EXCEPTION 'day backfill unavailable'; END IF;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.begin_activity_day_backfill(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.begin_activity_day_backfill(uuid) TO {processor}")
    op.execute(r"""CREATE FUNCTION analytics.invalidate_stale_activity_day(p_subject uuid,p_day date,p_run uuid) RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,analytics AS $$
      BEGIN
        IF NOT analytics.lock_activity_projection_run(p_run) THEN RAISE EXCEPTION 'activity run unavailable'; END IF;
        IF EXISTS(SELECT 1 FROM analytics.subject_fences f WHERE f.subject_ref=p_subject AND f.deleted) THEN RETURN; END IF;
        UPDATE analytics.activity_projection_day_sources s SET generation=s.generation+1,state='dirty',
          claim_id=NULL,owner=NULL,lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL,
          attempts=0,next_attempt_at=clock_timestamp(),last_failure=NULL
          WHERE s.subject_ref=p_subject AND s.local_day=p_day AND s.metric_version='activity-v1' AND s.state='ready'
          AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_days d WHERE d.subject_ref=s.subject_ref
            AND d.local_day=s.local_day AND d.metric_version=s.metric_version AND d.run_id=p_run AND d.generation=s.generation);
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.invalidate_stale_activity_day(uuid,date,uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.invalidate_stale_activity_day(uuid,date,uuid) TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.activity_projection_day_sources,analytics.activity_projection_days,analytics.activity_projection_day_backfill TO {processor},{query}")
    op.execute(f"GRANT UPDATE (state,claim_id,owner,lease_expires_at,claim_generation,claim_epoch,claim_run_id,attempts,next_attempt_at,last_failure) ON analytics.activity_projection_day_sources TO {processor}")
    op.execute(f"GRANT INSERT,UPDATE ON analytics.activity_projection_days TO {processor}")
    op.execute(f"GRANT UPDATE (phase,cursor_subject,cursor_bucket,cursor_metric) ON analytics.activity_projection_day_backfill TO {processor}")
    op.execute(f"GRANT SELECT,DELETE ON analytics.activity_projection_day_sources,analytics.activity_projection_days TO {privacy}")
    op.execute(f"GRANT SELECT (subject_ref) ON analytics.activity_projection_day_sources,analytics.activity_projection_days TO {status}")


def downgrade():
    raise RuntimeError("a011 is forward-only; preserve day projections and privacy state")
