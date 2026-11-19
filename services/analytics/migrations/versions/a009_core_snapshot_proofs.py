"""PROPOSED ONLY. Immutable subject proof receipts and bounded verification work."""
import re
from alembic import op

revision = "a009"
down_revision = "a008"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name) or len(name + "_transfer_status") > 63:
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    collector, processor, query, privacy, status = (quote(f"{name}_{part}") for part in
                                                     ("collector", "processor", "query", "privacy", "transfer_status"))
    op.execute("""CREATE TABLE analytics.core_snapshot_proofs (
        producer_namespace varchar(128) NOT NULL, proof_id uuid NOT NULL, subject_ref uuid NOT NULL,
        cut_revision bigint NOT NULL CHECK (cut_revision >= 0),
        entity_count integer NOT NULL CHECK (entity_count BETWEEN 0 AND 15000),
        entities_digest char(64) NOT NULL CHECK (entities_digest ~ '^[0-9a-f]{64}$'),
        source_created_at timestamptz NOT NULL,
        canonical_hash char(64) NOT NULL CHECK (canonical_hash ~ '^[0-9a-f]{64}$'),
        proof jsonb NOT NULL, status text NOT NULL DEFAULT 'pending'
          CHECK (status IN ('pending','verified','quarantined','superseded')),
        subject_epoch bigint CHECK (subject_epoch >= 0),
        attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        next_attempt_at timestamptz NOT NULL DEFAULT now(), last_failure text,
        PRIMARY KEY (producer_namespace,proof_id), UNIQUE (producer_namespace,subject_ref,cut_revision),
        CHECK (status <> 'verified' OR subject_epoch IS NOT NULL))""")
    op.execute("CREATE UNIQUE INDEX core_snapshot_one_verified ON analytics.core_snapshot_proofs (producer_namespace,subject_ref) WHERE status='verified'")
    op.execute("CREATE INDEX core_snapshot_pending ON analytics.core_snapshot_proofs (next_attempt_at,subject_ref,source_created_at) WHERE status='pending'")
    op.execute("CREATE INDEX core_snapshot_invalidate ON analytics.core_snapshot_proofs (producer_namespace,subject_ref,cut_revision) WHERE status IN ('pending','verified','quarantined')")
    op.execute("CREATE INDEX core_snapshot_privacy ON analytics.core_snapshot_proofs (subject_ref)")
    op.execute("CREATE INDEX core_projection_subject_revision ON analytics.core_transfer_projection (producer_namespace,subject_ref,revision)")
    op.execute('CREATE INDEX core_snapshot_entity_scan ON analytics.core_transfer_entities (producer_namespace,subject_ref,entity_key COLLATE "C",revision DESC)')
    op.execute(r"""CREATE FUNCTION analytics.invalidate_core_snapshot_proofs(p_namespace text,p_subject uuid,p_revision bigint)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE lock_key bigint;
        BEGIN
          IF p_namespace IS NULL OR p_namespace !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
             OR p_subject IS NULL OR p_revision IS NULL OR p_revision < 0 THEN
            RAISE EXCEPTION 'invalid snapshot invalidation arguments';
          END IF;
          lock_key := ('x' || substr(encode(sha256(convert_to('analytics:subject-lock:v1','UTF8')
                         || decode('00','hex') || uuid_send(p_subject)), 'hex'), 1, 16))::bit(64)::bigint;
          PERFORM pg_advisory_xact_lock(lock_key);
          UPDATE analytics.core_snapshot_proofs SET status='pending',attempts=0,
              next_attempt_at=clock_timestamp(),last_failure=NULL
            WHERE producer_namespace=p_namespace AND subject_ref=p_subject AND cut_revision>=p_revision
              AND status IN ('pending','verified','quarantined');
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.invalidate_core_snapshot_proofs(text,uuid,bigint) FROM PUBLIC")
    op.execute(r"""CREATE FUNCTION analytics.core_snapshot_fact_inserted() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, analytics AS $$
        DECLARE raw_revision text;
        BEGIN
          raw_revision := NEW.accepted_event->>'revision';
          IF jsonb_typeof(NEW.accepted_event->'revision') IS DISTINCT FROM 'number'
             OR raw_revision IS NULL OR raw_revision !~ '^(0|[1-9][0-9]{0,18})$'
             OR raw_revision::numeric > 9223372036854775807 THEN
            RAISE EXCEPTION 'invalid Core transfer revision';
          END IF;
          PERFORM analytics.invalidate_core_snapshot_proofs(NEW.producer_namespace,NEW.subject_ref,raw_revision::bigint);
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.core_snapshot_fact_inserted() FROM PUBLIC")
    op.execute("""CREATE TRIGGER core_snapshot_fact_inserted AFTER INSERT ON analytics.activity_facts
        FOR EACH ROW WHEN (NEW.source_kind='core_transfer_v1') EXECUTE FUNCTION analytics.core_snapshot_fact_inserted()""")
    op.execute(f"GRANT SELECT ON analytics.core_snapshot_proofs TO {collector}")
    op.execute(f"GRANT INSERT (producer_namespace,proof_id,subject_ref,cut_revision,entity_count,entities_digest,source_created_at,canonical_hash,proof) ON analytics.core_snapshot_proofs TO {collector}")
    op.execute(f"GRANT SELECT ON analytics.core_snapshot_proofs TO {processor}, {query}")
    op.execute(f"GRANT UPDATE (status,subject_epoch,attempts,next_attempt_at,last_failure) ON analytics.core_snapshot_proofs TO {processor}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.core_snapshot_proofs TO {privacy}")
    op.execute(f"GRANT SELECT (subject_ref) ON analytics.core_snapshot_proofs TO {status}")


def downgrade():
    raise RuntimeError("a009 is forward-only; preserve snapshot proofs and privacy obligations")
