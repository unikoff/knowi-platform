"""Expand Analytics with isolated business and control state for live windows."""
import re

from alembic import op

revision = "a015"
down_revision = "a014"
branch_labels = depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    collector, processor, privacy = (quote(f"{name}_{role}") for role in ("collector", "processor", "privacy"))

    op.execute("""CREATE FUNCTION analytics.valid_student_window_stats(stats jsonb)
        RETURNS boolean LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
        SELECT CASE WHEN jsonb_typeof(stats) <> 'array' THEN false
                    WHEN jsonb_array_length(stats) NOT BETWEEN 1 AND 32 THEN false
                    ELSE (
            SELECT bool_and(valid) AND sum(active_ms) <= 900000
                AND count(DISTINCT (tool, course, lesson)) = count(*)
                AND array_agg(position ORDER BY tool COLLATE "C", course COLLATE "C", lesson COLLATE "C")
                    = array_agg(position ORDER BY position)
            FROM (
                SELECT position, item->>'tool_id' AS tool,
                    coalesce(item->>'course_id','') AS course, coalesce(item->>'lesson_id','') AS lesson,
                    CASE WHEN item->>'active_ms' ~ '^[1-9][0-9]{0,5}$'
                         THEN (item->>'active_ms')::int ELSE 0 END AS active_ms,
                    CASE WHEN jsonb_typeof(item) <> 'object' THEN false ELSE
                        item ?& ARRAY['tool_id','course_id','lesson_id','active_ms']
                        AND item - ARRAY['tool_id','course_id','lesson_id','active_ms'] = '{}'::jsonb
                        AND jsonb_typeof(item->'tool_id') = 'string'
                        AND item->>'tool_id' IN (
                            'workspace','lessons','theory','practice','interpreter','account',
                            'account-profile','account-statistics','account-history','account-booking',
                            'account-settings','account-payments','account-achievements','catalog',
                            'catalog-preview','course-about','course-personal'
                        )
                        AND (item->'course_id' = 'null'::jsonb OR (
                            jsonb_typeof(item->'course_id') = 'string'
                            AND item->>'course_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                            AND item->>'course_id' <> 'platform-tools'
                        ))
                        AND (item->'lesson_id' = 'null'::jsonb OR (
                            jsonb_typeof(item->'lesson_id') = 'string'
                            AND item->>'lesson_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                            AND item->'course_id' <> 'null'::jsonb
                            AND item->>'tool_id' IN ('theory','practice')
                        ))
                        AND jsonb_typeof(item->'active_ms') = 'number'
                        AND item->>'active_ms' ~ '^[1-9][0-9]{0,5}$'
                    END AS valid
                FROM jsonb_array_elements(stats) WITH ORDINALITY AS parts(item,position)
            ) positions
        ) END
        $$""")
    op.execute("""CREATE TABLE analytics.student_activity_window_control (
        student_id uuid NOT NULL,
        activity_date date NOT NULL CHECK (activity_date BETWEEN DATE '2015-01-01' AND DATE '2100-12-31'),
        slot_no smallint NOT NULL CHECK (slot_no BETWEEN 1 AND 96),
        writer_id uuid,
        writer_generation bigint CHECK (writer_generation BETWEEN 1 AND 9007199254740991),
        server_epoch bigint NOT NULL DEFAULT 1 CHECK (server_epoch BETWEEN 1 AND 9007199254740991),
        seq bigint CHECK (seq BETWEEN 1 AND 9007199254740991),
        payload_hash text CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
        revision bigint CHECK (revision > 0),
        catalog_versions jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(catalog_versions)='object'),
        client_final boolean NOT NULL DEFAULT false,
        rebase_at timestamptz,
        phase text NOT NULL DEFAULT 'pending_reconciliation' CHECK (
            phase IN ('accumulating','pending_reconciliation','verified','repaired','incomplete_coverage')
        ),
        source_generation bigint NOT NULL DEFAULT 0 CHECK (source_generation BETWEEN 0 AND 9007199254740991),
        change_generation bigint NOT NULL DEFAULT 0 CHECK (change_generation BETWEEN 0 AND 9007199254740991),
        reconciled_generation bigint CHECK (reconciled_generation BETWEEN 0 AND source_generation),
        coverage_reasons text[] NOT NULL DEFAULT '{}',
        recheck_at timestamptz,
        reconciliation_evidence jsonb CHECK (
            jsonb_typeof(reconciliation_evidence)='object' AND octet_length(reconciliation_evidence::text)<=32768
        ),
        CHECK ((writer_id IS NULL) = (writer_generation IS NULL)),
        CHECK ((seq IS NULL) = (payload_hash IS NULL)),
        CHECK (seq IS NULL OR (writer_id IS NOT NULL AND revision IS NOT NULL)),
        PRIMARY KEY (student_id,activity_date,slot_no)
    )""")
    op.execute("""CREATE INDEX student_activity_window_dirty
        ON analytics.student_activity_window_control(activity_date,slot_no,student_id)
        WHERE phase IN ('accumulating','pending_reconciliation')""")
    op.execute("""CREATE INDEX student_activity_window_recheck
        ON analytics.student_activity_window_control(recheck_at,activity_date,slot_no,student_id)
        WHERE phase='incomplete_coverage' AND recheck_at IS NOT NULL""")
    op.execute("""CREATE TABLE analytics.student_activity_windows (
        student_id uuid NOT NULL,
        activity_date date NOT NULL CHECK (activity_date BETWEEN DATE '2015-01-01' AND DATE '2100-12-31'),
        slot_no smallint NOT NULL CHECK (slot_no BETWEEN 1 AND 96),
        stats jsonb NOT NULL CHECK (analytics.valid_student_window_stats(stats)),
        revision bigint NOT NULL CHECK (revision > 0),
        PRIMARY KEY (student_id,activity_date,slot_no),
        FOREIGN KEY (student_id,activity_date,slot_no) REFERENCES
            analytics.student_activity_window_control(student_id,activity_date,slot_no) ON DELETE CASCADE
    )""")

    control = "analytics.student_activity_window_control"
    windows = "analytics.student_activity_windows"
    op.execute(f"REVOKE ALL ON {control},{windows} FROM PUBLIC")
    op.execute(f"ALTER TABLE {control} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY student_window_control_read ON {control} FOR SELECT TO {collector} USING (true)")
    collector_phases = "phase IN ('accumulating','pending_reconciliation')"
    op.execute(f"""CREATE POLICY student_window_control_insert ON {control}
        FOR INSERT TO {collector} WITH CHECK ({collector_phases})""")
    op.execute(f"""CREATE POLICY student_window_control_update ON {control}
        FOR UPDATE TO {collector} USING (true) WITH CHECK ({collector_phases})""")
    op.execute(f"""CREATE POLICY student_window_control_processing ON {control}
        TO {processor},{privacy} USING (true) WITH CHECK (true)""")
    op.execute(f"""GRANT SELECT ON {control} TO {collector},{processor},{privacy}""")
    collector_fields = """writer_id,writer_generation,server_epoch,seq,payload_hash,revision,catalog_versions,
        client_final,rebase_at,phase,source_generation,change_generation,coverage_reasons"""
    op.execute(f"""GRANT INSERT (student_id,activity_date,slot_no,{collector_fields}),
        UPDATE ({collector_fields}) ON {control} TO {collector}""")
    op.execute(f"GRANT INSERT,UPDATE ON {control} TO {processor}")
    op.execute(f"GRANT DELETE ON {control} TO {privacy}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON {windows} TO {collector},{processor}")
    op.execute(f"GRANT DELETE ON {windows} TO {processor}")
    op.execute(f"GRANT SELECT ON {windows} TO {privacy}")
    op.execute("REVOKE ALL ON FUNCTION analytics.valid_student_window_stats(jsonb) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.valid_student_window_stats(jsonb) TO {collector},{processor}")

    # An a014 Privacy binary does not know these tables. It may serve an empty
    # expansion, but cannot acknowledge deletion while this lifecycle has data.
    op.execute("""CREATE FUNCTION analytics.require_student_windows_purged() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
          IF EXISTS (SELECT FROM analytics.student_activity_window_control
                     WHERE student_id=NEW.subject_ref) THEN
            RAISE EXCEPTION 'student window purge required before completing deletion';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION analytics.require_student_windows_purged() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION analytics.require_student_windows_purged() TO {privacy}")
    op.execute("""CREATE TRIGGER student_windows_before_privacy_complete
        BEFORE UPDATE OF phase ON analytics.privacy_receipts FOR EACH ROW
        WHEN (NEW.phase='complete') EXECUTE FUNCTION analytics.require_student_windows_purged()""")


def downgrade():
    raise RuntimeError("a015 is forward-only; keep a015-aware Privacy until retained windows are purged")
