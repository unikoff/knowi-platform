"""PROPOSED ONLY. Common activity-v1 buckets and resumable backfill."""
import re
from alembic import op

revision = "a010"
down_revision = "a009"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if conn.exec_driver_sql("SHOW server_encoding").scalar_one().upper() != "UTF8":
        raise ValueError("activity projection requires UTF8 database encoding")
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name) or len(name + "_transfer_status") > 63:
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    processor, query, privacy, status = (quote(f"{name}_{part}") for part in ("processor", "query", "privacy", "transfer_status"))
    op.execute("""CREATE TABLE analytics.activity_projection_buckets (
        subject_ref uuid NOT NULL, bucket_start timestamptz NOT NULL,
        metric_version text NOT NULL DEFAULT 'activity-v1' CHECK (metric_version='activity-v1'),
        generation bigint NOT NULL CHECK (generation>=1), projection_generation bigint,
        subject_epoch bigint CHECK (subject_epoch>=0),
        state text NOT NULL CHECK (state IN ('dirty','claimed','ready','quarantined')),
        claim_id uuid, owner uuid, lease_expires_at timestamptz, claim_generation bigint, claim_epoch bigint, claim_run_id uuid,
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
        next_attempt_at timestamptz NOT NULL DEFAULT now(), last_failure text,
        winner_intervals jsonb, presence_intervals jsonb, presence_points jsonb, contributions jsonb, presence_labels jsonb,
        has_presence boolean,
        compact_item_count integer CHECK (compact_item_count BETWEEN 0 AND 30000),
        edge_item_count integer CHECK (edge_item_count BETWEEN 0 AND 40000),
        compact_json_bytes bigint GENERATED ALWAYS AS
            (octet_length(contributions::text)::bigint+octet_length(presence_labels::text)::bigint) STORED,
        edge_json_bytes bigint GENERATED ALWAYS AS
            (octet_length(winner_intervals::text)::bigint+octet_length(presence_intervals::text)::bigint+octet_length(presence_points::text)::bigint) STORED,
        PRIMARY KEY (subject_ref,bucket_start,metric_version),
        CHECK (extract(epoch from bucket_start)::numeric % 900 = 0),
        CHECK (projection_generation IS NULL OR (projection_generation>=1 AND projection_generation<=generation)),
        CHECK (state<>'claimed' OR (claim_id IS NOT NULL AND owner IS NOT NULL AND lease_expires_at IS NOT NULL AND claim_generation IS NOT NULL AND claim_run_id IS NOT NULL)),
        CHECK (state<>'ready' OR (projection_generation IS NOT NULL AND projection_generation=generation AND subject_epoch IS NOT NULL
            AND winner_intervals IS NOT NULL AND presence_intervals IS NOT NULL AND presence_points IS NOT NULL
            AND contributions IS NOT NULL AND presence_labels IS NOT NULL
            AND has_presence IS NOT NULL AND compact_item_count IS NOT NULL AND edge_item_count IS NOT NULL
            AND compact_json_bytes IS NOT NULL AND edge_json_bytes IS NOT NULL
            AND compact_json_bytes+edge_json_bytes<=8388608)),
        CHECK (state<>'ready' OR CASE WHEN
            jsonb_typeof(winner_intervals)='array' AND jsonb_typeof(presence_intervals)='array'
            AND jsonb_typeof(presence_points)='array' AND jsonb_typeof(contributions)='array'
            AND jsonb_typeof(presence_labels)='array' THEN
            has_presence=(jsonb_array_length(presence_labels)>0)
            AND compact_item_count=jsonb_array_length(contributions)+jsonb_array_length(presence_labels)
            AND edge_item_count=jsonb_array_length(winner_intervals)+jsonb_array_length(presence_intervals)+jsonb_array_length(presence_points)
            ELSE false END))""")
    op.execute("CREATE INDEX activity_projection_time_subject ON analytics.activity_projection_buckets (bucket_start,subject_ref,metric_version)")
    op.execute("CREATE INDEX activity_projection_work ON analytics.activity_projection_buckets (next_attempt_at,bucket_start,subject_ref) WHERE state IN ('dirty','claimed')")
    op.execute("CREATE INDEX activity_projection_outstanding ON analytics.activity_projection_buckets (state) WHERE state<>'ready'")
    op.execute("""CREATE TABLE analytics.activity_projection_backfill (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), run_id uuid,
        phase text NOT NULL CHECK (phase IN ('scanning','draining','complete')),
        cursor_namespace varchar(128), cursor_event_id uuid,
        CHECK ((cursor_namespace IS NULL)=(cursor_event_id IS NULL)))""")
    op.execute("INSERT INTO analytics.activity_projection_backfill(singleton,phase) VALUES(true,'scanning')")
    op.execute(r"""CREATE FUNCTION analytics.mark_activity_projection_fact(p_subject uuid,p_started timestamptz,p_ended timestamptz,p_kind text)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE lock_key bigint; first_ms bigint; end_ms bigint; first_bucket bigint; last_bucket bigint; b bigint;
                is_deleted boolean; existing_state text; max_pending integer; outstanding integer;
        BEGIN
          IF p_subject IS NULL OR p_started IS NULL OR p_ended IS NULL OR p_kind IS NULL OR p_kind NOT IN ('course_opened','activity_segment') THEN
            RAISE EXCEPTION 'invalid activity fact';
          END IF;
          first_ms := floor(extract(epoch FROM p_started)*1000)::bigint;
          end_ms := floor(extract(epoch FROM p_ended)*1000)::bigint;
          IF (p_kind='course_opened' AND end_ms<>first_ms) OR
             (p_kind='activity_segment' AND (end_ms<=first_ms OR end_ms-first_ms>60000)) THEN
            RAISE EXCEPTION 'invalid activity interval';
          END IF;
          lock_key := ('x' || substr(encode(sha256(convert_to('analytics:subject-lock:v1','UTF8')
                         || decode('00','hex') || uuid_send(p_subject)), 'hex'), 1, 16))::bit(64)::bigint;
          PERFORM pg_advisory_xact_lock(lock_key);
          SELECT deleted INTO is_deleted FROM analytics.subject_fences WHERE subject_ref=p_subject;
          IF coalesce(is_deleted,false) THEN RETURN false; END IF;
          SELECT max_outstanding_jobs INTO max_pending FROM analytics.admission_budget WHERE singleton=true FOR UPDATE;
          IF max_pending IS NULL THEN RAISE EXCEPTION 'activity admission policy unavailable'; END IF;
          first_bucket := floor(first_ms::numeric/900000)::bigint * 900;
          last_bucket := CASE WHEN p_kind='course_opened' THEN first_bucket
                         ELSE floor((end_ms-1)::numeric/900000)::bigint * 900 END;
          IF last_bucket<first_bucket OR last_bucket-first_bucket>900 THEN RAISE EXCEPTION 'invalid bucket span'; END IF;
          FOR b IN SELECT DISTINCT x FROM (VALUES (first_bucket),(last_bucket)) v(x) ORDER BY x LOOP
            SELECT state INTO existing_state FROM analytics.activity_projection_buckets
              WHERE subject_ref=p_subject AND bucket_start=to_timestamp(b) AND metric_version='activity-v1';
            IF existing_state IS NULL OR existing_state='ready' THEN
              SELECT count(*) INTO outstanding FROM (SELECT 1 FROM analytics.activity_projection_buckets
                  WHERE state<>'ready' LIMIT max_pending) pending;
              IF outstanding >= max_pending THEN RAISE EXCEPTION USING ERRCODE='PCA01', MESSAGE='activity projection capacity exceeded'; END IF;
            END IF;
            INSERT INTO analytics.activity_projection_buckets(subject_ref,bucket_start,metric_version,generation,state)
              VALUES (p_subject,to_timestamp(b),'activity-v1',1,'dirty')
              ON CONFLICT (subject_ref,bucket_start,metric_version) DO UPDATE SET
                generation=analytics.activity_projection_buckets.generation+1,state='dirty',claim_id=NULL,owner=NULL,
                lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL,attempts=0,
                next_attempt_at=clock_timestamp(),last_failure=NULL;
          END LOOP;
          RETURN true;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.mark_activity_projection_fact(uuid,timestamptz,timestamptz,text) FROM PUBLIC")
    op.execute(r"""CREATE FUNCTION analytics.backfill_activity_projection_fact(p_namespace text,p_event_id uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE fact record; raw_end text;
        BEGIN
          IF p_namespace IS NULL OR p_namespace !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR p_event_id IS NULL THEN
            RAISE EXCEPTION 'invalid activity backfill identity';
          END IF;
          SELECT subject_ref,started_at,accepted_event INTO fact FROM analytics.activity_facts
            WHERE producer_namespace=p_namespace AND event_id=p_event_id AND source_kind='browser_v2';
          IF NOT FOUND THEN RETURN false; END IF;
          raw_end := fact.accepted_event #>> '{event,ended_at}';
          IF raw_end IS NULL OR raw_end !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
            RAISE EXCEPTION 'invalid activity timestamp';
          END IF;
          RETURN analytics.mark_activity_projection_fact(fact.subject_ref,fact.started_at,raw_end::timestamptz,
                    fact.accepted_event #>> '{event,type}');
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.backfill_activity_projection_fact(text,uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.backfill_activity_projection_fact(text,uuid) TO {processor}")
    op.execute(r"""CREATE FUNCTION analytics.activity_projection_fact_inserted() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE raw_end text; kind text; accepted boolean;
        BEGIN
          raw_end := NEW.accepted_event #>> '{event,ended_at}';
          kind := NEW.accepted_event #>> '{event,type}';
          IF raw_end IS NULL OR raw_end !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
            RAISE EXCEPTION 'invalid activity timestamp';
          END IF;
          accepted := analytics.mark_activity_projection_fact(NEW.subject_ref,NEW.started_at,raw_end::timestamptz,kind);
          IF NOT accepted THEN RAISE EXCEPTION 'activity subject is deleted'; END IF;
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.activity_projection_fact_inserted() FROM PUBLIC")
    op.execute("""CREATE TRIGGER activity_projection_fact_inserted AFTER INSERT ON analytics.activity_facts
        FOR EACH ROW WHEN (NEW.source_kind='browser_v2') EXECUTE FUNCTION analytics.activity_projection_fact_inserted()""")
    op.execute(r"""CREATE FUNCTION analytics.begin_activity_projection_backfill(p_expected uuid,p_new uuid)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE current_id uuid;
        BEGIN
          IF p_new IS NULL THEN RAISE EXCEPTION 'new rebuild identity required'; END IF;
          SELECT run_id INTO current_id FROM analytics.activity_projection_backfill WHERE singleton=true FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'backfill state missing'; END IF;
          IF current_id IS NOT DISTINCT FROM p_new THEN RETURN; END IF;
          IF current_id IS DISTINCT FROM p_expected THEN RAISE EXCEPTION 'backfill identity changed'; END IF;
          UPDATE analytics.activity_projection_backfill SET run_id=p_new,phase='scanning',
              cursor_namespace=NULL,cursor_event_id=NULL WHERE singleton=true;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.begin_activity_projection_backfill(uuid,uuid) FROM PUBLIC")
    op.execute(r"""CREATE FUNCTION analytics.lock_activity_projection_run(p_expected uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE current_id uuid; current_phase text;
        BEGIN
          IF p_expected IS NULL THEN RETURN false; END IF;
          SELECT run_id,phase INTO current_id,current_phase FROM analytics.activity_projection_backfill
            WHERE singleton=true FOR SHARE;
          IF NOT FOUND THEN RETURN false; END IF;
          RETURN current_id IS NOT DISTINCT FROM p_expected AND current_phase='complete';
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.lock_activity_projection_run(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.lock_activity_projection_run(uuid) TO {query}")
    op.execute(f"GRANT SELECT ON analytics.activity_projection_buckets TO {processor}")
    op.execute(f"GRANT UPDATE (state,claim_id,owner,lease_expires_at,claim_generation,claim_epoch,claim_run_id,attempts,next_attempt_at,last_failure,projection_generation,subject_epoch,winner_intervals,presence_intervals,presence_points,contributions,presence_labels,has_presence,compact_item_count,edge_item_count) ON analytics.activity_projection_buckets TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.activity_projection_backfill TO {processor}")
    op.execute(f"GRANT UPDATE (phase,cursor_namespace,cursor_event_id) ON analytics.activity_projection_backfill TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.activity_projection_buckets, analytics.activity_projection_backfill TO {query}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.activity_projection_buckets TO {privacy}")
    op.execute(f"GRANT SELECT (subject_ref) ON analytics.activity_projection_buckets TO {status}")


def downgrade():
    raise RuntimeError("a010 is forward-only; preserve bucket data, claims and privacy state")
