"""Versioned dirty replay state and bounded live/replay work classes."""
import re
from alembic import op

revision = "a005"
down_revision = "a004"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    processor, privacy, query = (quote(f"{name}_{role}") for role in ("processor", "privacy", "query"))
    op.execute("ALTER TABLE analytics.processing_jobs ADD COLUMN work_class text NOT NULL DEFAULT 'live' CHECK (work_class IN ('live','replay'))")
    op.execute("ALTER TABLE analytics.processing_jobs ADD COLUMN failed_replay_id uuid")
    op.execute("CREATE INDEX processing_jobs_replay_outstanding ON analytics.processing_jobs (work_class,status) WHERE status <> 'completed'")
    op.execute("CREATE INDEX activity_replay_cursor ON analytics.activity_facts (subject_ref,producer_namespace,event_id)")
    op.execute("CREATE INDEX processing_receipts_replay_cursor ON analytics.processing_receipts (consumer,consumer_version,subject_ref,producer_namespace,event_id)")
    # Fail closed on historical cross-subject corruption before replay is enabled.
    # No mismatched receipt/job is reassigned or removed automatically.
    op.execute("CREATE UNIQUE INDEX activity_fact_subject_identity ON analytics.activity_facts (producer_namespace,event_id,subject_ref)")
    op.execute("ALTER TABLE analytics.activity_facts ADD CONSTRAINT activity_fact_subject_identity UNIQUE USING INDEX activity_fact_subject_identity")
    for table, constraint in (("processing_receipts", "receipt_fact_subject_fk"),
                              ("processing_jobs", "job_fact_subject_fk")):
        op.execute(f"""ALTER TABLE analytics.{table} ADD CONSTRAINT {constraint}
            FOREIGN KEY (producer_namespace,event_id,subject_ref)
            REFERENCES analytics.activity_facts (producer_namespace,event_id,subject_ref)
            ON DELETE CASCADE NOT VALID""")
        op.execute(f"ALTER TABLE analytics.{table} VALIDATE CONSTRAINT {constraint}")
    op.execute("ALTER TABLE analytics.processing_leases ADD COLUMN live_streak integer NOT NULL DEFAULT 0 CHECK (live_streak BETWEEN 0 AND 3)")
    op.execute("ALTER TABLE analytics.admission_budget ADD COLUMN lock_nonce boolean NOT NULL DEFAULT false")
    op.execute("""CREATE TABLE analytics.processing_replays (
        consumer varchar(128) NOT NULL, consumer_version integer NOT NULL,
        subject_ref uuid NOT NULL, request_id uuid NOT NULL,
        generation bigint NOT NULL CHECK (generation >= 1),
        subject_epoch bigint NOT NULL CHECK (subject_epoch >= 0),
        phase text NOT NULL CHECK (phase IN ('cleanup','scan','drain','complete')),
        cursor_namespace varchar(128), cursor_event_id uuid,
        CHECK ((cursor_namespace IS NULL) = (cursor_event_id IS NULL)),
        PRIMARY KEY (consumer,consumer_version,subject_ref,request_id),
        UNIQUE (consumer,consumer_version,subject_ref,generation)
    )""")
    op.execute("CREATE UNIQUE INDEX one_active_processing_replay ON analytics.processing_replays (consumer,consumer_version,subject_ref) WHERE phase <> 'complete'")
    op.execute(r"""CREATE FUNCTION analytics.replay_cleanup(p_consumer text, p_version integer, p_subject uuid,
            p_request uuid, p_generation bigint, p_epoch bigint, p_limit integer)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE deleted_count integer; lock_key bigint; actual_epoch bigint; tombstoned boolean;
        BEGIN
          IF p_consumer IS NULL OR p_consumer NOT IN ('synthetic.activity','synthetic.independent')
             OR p_version IS DISTINCT FROM 1 OR p_subject IS NULL OR p_request IS NULL
             OR p_generation IS NULL OR p_generation < 1 OR p_epoch IS NULL OR p_epoch < 0
             OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
            RAISE EXCEPTION 'invalid cleanup arguments';
          END IF;
          lock_key := ('x' || substr(encode(sha256(convert_to('analytics:subject-lock:v1','UTF8')
                                  || decode('00','hex') || uuid_send(p_subject)), 'hex'), 1, 16))::bit(64)::bigint;
          PERFORM pg_advisory_xact_lock(lock_key);
          SELECT epoch,deleted INTO actual_epoch,tombstoned FROM analytics.subject_fences WHERE subject_ref=p_subject;
          IF coalesce(tombstoned,false) OR coalesce(actual_epoch,0) <> p_epoch THEN
            RAISE EXCEPTION 'subject fence changed';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM analytics.processing_replays WHERE consumer=p_consumer
              AND consumer_version=p_version AND subject_ref=p_subject AND request_id=p_request
              AND generation=p_generation AND subject_epoch=p_epoch AND phase='cleanup')
             OR EXISTS (SELECT 1 FROM analytics.processing_replays WHERE consumer=p_consumer
              AND consumer_version=p_version AND subject_ref=p_subject AND generation>p_generation) THEN
            RAISE EXCEPTION 'current cleanup handle required';
          END IF;
          DELETE FROM analytics.processing_results WHERE consumer=p_consumer
            AND consumer_version=p_version AND subject_ref=p_subject;
          WITH removed AS (DELETE FROM analytics.processing_receipts WHERE ctid IN (
            SELECT r.ctid FROM analytics.processing_receipts r JOIN analytics.activity_facts f
              ON f.producer_namespace=r.producer_namespace AND f.event_id=r.event_id
            WHERE r.consumer=p_consumer AND r.consumer_version=p_version
              AND r.subject_ref=p_subject AND f.subject_ref=p_subject
            ORDER BY r.producer_namespace,r.event_id LIMIT p_limit) RETURNING 1)
            SELECT count(*) INTO deleted_count FROM removed;
          RETURN deleted_count;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.replay_cleanup(text,integer,uuid,uuid,bigint,bigint,integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.replay_cleanup(text,integer,uuid,uuid,bigint,bigint,integer) TO {processor}")
    op.execute(f"GRANT SELECT, INSERT ON analytics.processing_replays TO {processor}")
    op.execute(f"GRANT UPDATE (phase,cursor_namespace,cursor_event_id) ON analytics.processing_replays TO {processor}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.processing_replays TO {privacy}")
    op.execute(f"GRANT SELECT (consumer,consumer_version,subject_ref,phase) ON analytics.processing_replays TO {query}")
    op.execute(f"GRANT INSERT (producer_namespace,event_id,consumer,consumer_version,subject_ref,status,work_class) ON analytics.processing_jobs TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.admission_budget TO {processor}")
    op.execute(f"GRANT UPDATE (lock_nonce) ON analytics.admission_budget TO {processor}")


def downgrade():
    raise RuntimeError("a005 is forward-only; preserve replay generations and facts")
