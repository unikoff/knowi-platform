"""Capture accepted fact changes for the resumable historical windows transfer."""

import re

from alembic import op

revision = "a016"
down_revision = "a015"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    processor = quote(name + "_processor")
    privacy = quote(name + "_privacy")

    op.execute("""ALTER TABLE analytics.student_activity_window_control
        ADD COLUMN history_backfill_owned boolean NOT NULL DEFAULT false""")
    op.execute("""CREATE TABLE analytics.window_backfill_gate (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        v2_closed boolean NOT NULL DEFAULT false
    )""")
    op.execute("INSERT INTO analytics.window_backfill_gate DEFAULT VALUES")
    op.execute("""CREATE TABLE analytics.window_backfill_run (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        run_id uuid NOT NULL UNIQUE,
        t0 timestamptz NOT NULL,
        snapshot text NOT NULL,
        snapshot_at timestamptz NOT NULL,
        snapshot_position bigint NOT NULL,
        accepted_position bigint NOT NULL,
        applied_position bigint NOT NULL,
        final_position bigint,
        scan_complete boolean NOT NULL DEFAULT false,
        scanned_facts bigint NOT NULL DEFAULT 0,
        CHECK (snapshot_position <= applied_position AND applied_position <= accepted_position),
        CHECK (final_position IS NULL OR final_position <= accepted_position)
    )""")
    op.execute("""CREATE TABLE analytics.window_backfill_scan_cursor (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        subject_ref uuid NOT NULL,
        scan_namespace varchar(128) NOT NULL,
        scan_event uuid NOT NULL
    )""")
    op.execute("""CREATE TABLE analytics.window_backfill_journal (
        position bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        subject_ref uuid,
        measured_start timestamptz
    )""")
    op.execute("""CREATE INDEX window_backfill_journal_subject
        ON analytics.window_backfill_journal(subject_ref) WHERE subject_ref IS NOT NULL""")
    op.execute("""CREATE TABLE analytics.window_backfill_work (
        student_id uuid NOT NULL,
        activity_date date NOT NULL,
        slot_no smallint NOT NULL CHECK (slot_no BETWEEN 1 AND 96),
        result text,
        managed boolean NOT NULL DEFAULT false,
        post_t0_source_sha256 text CHECK (post_t0_source_sha256 ~ '^[0-9a-f]{64}$'),
        revision bigint,
        PRIMARY KEY (student_id,activity_date,slot_no)
    )""")
    op.execute("""CREATE INDEX window_backfill_pending
        ON analytics.window_backfill_work(student_id,activity_date,slot_no) WHERE result IS NULL""")
    tables = ("analytics.window_backfill_gate", "analytics.window_backfill_run",
              "analytics.window_backfill_journal", "analytics.window_backfill_work",
              "analytics.window_backfill_scan_cursor")
    op.execute(f"REVOKE ALL ON {','.join(tables)} FROM PUBLIC")
    op.execute(f"GRANT SELECT,UPDATE ON {tables[0]} TO {processor}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON {tables[1]} TO {processor}")
    op.execute(f"GRANT SELECT,DELETE ON {tables[2]} TO {processor}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON {tables[3]} TO {processor}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {tables[4]} TO {processor}")
    op.execute(f"GRANT SELECT ON analytics.activity_credit_windows,analytics.activity_credit_days TO {processor}")

    # A short exclusive barrier waits for every already allocated position.
    # Writers share the lock, so unrelated subject intake does not serialize.
    # Positions may commit out of order between barriers and never prove a cut alone.
    op.execute("""CREATE FUNCTION analytics.capture_window_backfill_fact() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE
            fact analytics.activity_facts%ROWTYPE;
            closed boolean;
        BEGIN
            IF session_user=current_user AND current_setting('analytics.window_backfill_restore',true)='on' THEN
                RETURN NULL;
            END IF;
            IF TG_OP = 'DELETE' THEN fact := OLD; ELSE fact := NEW; END IF;
            IF EXISTS (SELECT FROM analytics.subject_fences
                       WHERE subject_ref=fact.subject_ref AND deleted) THEN
                IF TG_OP = 'DELETE' THEN RETURN NULL; END IF;
                RAISE EXCEPTION 'backfill source lifecycle is deleted' USING ERRCODE='55000';
            END IF;
            PERFORM pg_advisory_xact_lock_shared(280608, 1);
            SELECT v2_closed INTO STRICT closed
              FROM analytics.window_backfill_gate WHERE singleton FOR SHARE;
            IF closed AND TG_OP <> 'DELETE' AND fact.source_kind IN ('browser_v2','browser_tool_v1') THEN
                RAISE EXCEPTION 'legacy activity input was closed at the backfill barrier' USING ERRCODE='55000';
            END IF;
            INSERT INTO analytics.window_backfill_journal(subject_ref,measured_start)
              VALUES (fact.subject_ref, CASE WHEN fact.source_kind IN ('browser_v2','browser_tool_v1')
                AND fact.accepted_event#>>'{event,type}' IN ('activity_segment','tool_segment')
                THEN fact.started_at END);
            RETURN NULL;
        END $$""")
    op.execute("""CREATE TRIGGER window_backfill_fact_changed
        AFTER INSERT OR DELETE ON analytics.activity_facts FOR EACH ROW
        EXECUTE FUNCTION analytics.capture_window_backfill_fact()""")

    op.execute("""CREATE FUNCTION analytics.erase_window_backfill_subject() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            IF session_user=current_user AND current_setting('analytics.window_backfill_restore',true)='on' THEN
                RETURN NULL;
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.deleted THEN RETURN NULL; END IF;
            PERFORM pg_advisory_xact_lock_shared(280608, 1);
            -- Keep a position for the deletion without retaining a resumable lifecycle key.
            INSERT INTO analytics.window_backfill_journal DEFAULT VALUES;
            RETURN NULL;
        END $$""")
    op.execute("""CREATE TRIGGER window_backfill_subject_deleted
        AFTER INSERT OR UPDATE OF deleted ON analytics.subject_fences FOR EACH ROW
        WHEN (NEW.deleted) EXECUTE FUNCTION analytics.erase_window_backfill_subject()""")
    op.execute("""CREATE FUNCTION analytics.purge_window_backfill_page(p_subject uuid,p_limit integer)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          IF p_limit NOT BETWEEN 1 AND 1000 OR NOT EXISTS (
            SELECT FROM analytics.subject_fences WHERE subject_ref=p_subject AND deleted
          ) THEN RAISE EXCEPTION 'invalid historical Privacy purge request'; END IF;
          DELETE FROM analytics.window_backfill_scan_cursor WHERE subject_ref=p_subject;
          DELETE FROM analytics.window_backfill_work WHERE ctid IN (
            SELECT ctid FROM analytics.window_backfill_work WHERE student_id=p_subject
            ORDER BY activity_date,slot_no LIMIT p_limit);
          DELETE FROM analytics.window_backfill_journal WHERE ctid IN (
            SELECT ctid FROM analytics.window_backfill_journal WHERE subject_ref=p_subject
            ORDER BY position LIMIT p_limit);
          RETURN EXISTS(SELECT FROM analytics.window_backfill_work WHERE student_id=p_subject)
              OR EXISTS(SELECT FROM analytics.window_backfill_journal WHERE subject_ref=p_subject);
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.purge_window_backfill_page(uuid,integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.purge_window_backfill_page(uuid,integer) TO {privacy}")
    op.execute("""CREATE FUNCTION analytics.require_window_backfill_purged() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          IF EXISTS (SELECT FROM analytics.window_backfill_work WHERE student_id=NEW.subject_ref)
             OR EXISTS (SELECT FROM analytics.window_backfill_journal WHERE subject_ref=NEW.subject_ref)
             OR EXISTS (SELECT FROM analytics.window_backfill_scan_cursor WHERE subject_ref=NEW.subject_ref)
          THEN RAISE EXCEPTION 'historical work purge required before completing deletion'; END IF;
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.require_window_backfill_purged() FROM PUBLIC")
    op.execute("""CREATE TRIGGER window_backfill_before_privacy_complete
        BEFORE UPDATE OF phase ON analytics.privacy_receipts FOR EACH ROW
        WHEN (NEW.phase='complete') EXECUTE FUNCTION analytics.require_window_backfill_purged()""")
    op.execute("REVOKE ALL ON FUNCTION analytics.capture_window_backfill_fact() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION analytics.erase_window_backfill_subject() FROM PUBLIC")


def downgrade():
    raise RuntimeError("a016 is forward-only; preserve the journal and published commit cut")
