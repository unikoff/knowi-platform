"""Logical synthetic a007 snapshot; no reset, truncation or encrypted backup claim."""
import json

TABLES = ("activity_facts", "core_transfer_entities", "processing_jobs", "processing_receipts", "core_transfer_projection")


def snapshot(target, subject, *, proof=False, privacy_history=False, catalog_history=False, browser=False,
             day_projection=False, run_id=None, day_revision="a011"):
    import psycopg
    from psycopg import sql
    result = {}
    if day_revision not in {"a011", "a012"} or (day_revision == "a012" and not day_projection):
        raise ValueError("unsupported day snapshot revision")
    if day_projection and not browser:
        raise ValueError("day snapshot requires bucket snapshot")
    if day_projection:
        from uuid import UUID
        if not isinstance(run_id, UUID):
            raise ValueError("day snapshot requires known run identity")
    subjects = [str(item) for item in subject] if isinstance(subject, (list, tuple)) else [str(subject)]
    with psycopg.connect(target["dsn"], connect_timeout=3, options="-c statement_timeout=3000 -c default_transaction_read_only=on") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actual = conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        if actual != (target["name"], target["owner"]) or conn.execute("SELECT token FROM public.cross_service_sentinel").fetchall() != [(target["sentinel"],)]:
            raise ValueError("snapshot target guard failed")
        if day_projection and conn.execute("SELECT version_num FROM alembic_version").fetchall() != [(day_revision,)]:
            raise ValueError("day snapshot requires the exact declared revision")
        if day_projection:
            for table in ("activity_projection_backfill", "activity_projection_day_backfill"):
                if conn.execute(sql.SQL("SELECT run_id,phase FROM analytics.{} WHERE singleton=true").format(sql.Identifier(table))).fetchall() != [(run_id, "complete")]:
                    raise ValueError("day snapshot run is not complete")
            bucket_bad = conn.execute("""SELECT count(*) FROM analytics.activity_projection_buckets
                WHERE state<>'ready' OR projection_generation IS DISTINCT FROM generation""").fetchone()[0]
            day_bad = conn.execute("""SELECT count(*) FROM analytics.activity_projection_day_sources s
                LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
                LEFT JOIN analytics.subject_fences f USING(subject_ref)
                WHERE s.state<>'ready' OR d.run_id IS DISTINCT FROM %s OR d.generation IS DISTINCT FROM s.generation
                    OR d.subject_epoch IS DISTINCT FROM coalesce(f.epoch,0) OR coalesce(f.deleted,false)""", (run_id,)).fetchone()[0]
            if bucket_bad or day_bad:
                raise ValueError("day snapshot projections are not reconciled")
        tables = TABLES + (("core_snapshot_proofs",) if proof else ()) + (("processing_results", "processing_leases", "processing_replays", "admission_subject_windows", "activity_projection_buckets") if browser else ())
        for table in tables:
            result[table] = [row[0] for row in conn.execute(sql.SQL("SELECT row_to_json(t)::text FROM analytics.{} t WHERE subject_ref=ANY(%s::uuid[])").format(sql.Identifier(table)), (subjects,))]
        result["privacy_state"] = [row[0] for row in conn.execute("SELECT row_to_json(t)::text FROM analytics.privacy_state t")]
        if privacy_history:
            for table in ("subject_fences", "privacy_receipts"):
                result[table] = [row[0] for row in conn.execute(sql.SQL("SELECT row_to_json(t)::text FROM analytics.{} t").format(sql.Identifier(table)))]
        if catalog_history:
            result["catalog_versions"] = [row[0] for row in conn.execute("SELECT row_to_json(t)::text FROM analytics.catalog_versions t")]
        if browser:
            result["activity_projection_backfill"] = [row[0] for row in conn.execute("SELECT row_to_json(t)::text FROM analytics.activity_projection_backfill t")]
    if (not result["activity_facts"] or (not browser and not result["core_transfer_projection"])
            or (browser and (not result["activity_projection_buckets"] or len(result["activity_projection_backfill"]) != 1))
            or len(result["privacy_state"]) != 1):
        raise ValueError("incomplete old logical snapshot")
    return result


def verify_day_trigger(conn, target):
    """Validate the exact enabled security-definer trigger and its owner."""
    rows = conn.execute(r"""SELECT t.tgenabled,t.tgtype,pg_get_userbyid(p.proowner),p.prosecdef,
        EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
               WHERE a.grantee=0 AND a.privilege_type='EXECUTE'),
        EXISTS(SELECT 1 FROM unnest(coalesce(p.proconfig,ARRAY[]::text[])) AS cfg(setting)
               WHERE regexp_replace(cfg.setting,'\s+','','g')='search_path=pg_catalog,analytics')
        FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='analytics.activity_projection_buckets'::regclass
          AND t.tgname='activity_projection_bucket_changed' AND NOT t.tgisinternal
          AND t.tgfoid=to_regprocedure('analytics.activity_projection_bucket_changed()')""").fetchall()
    if rows != [("O", 29, target["owner"], True, False, True)]:
        raise ValueError("unsafe day invalidation trigger")


def verify_fresh_a011_target(conn, target, *, revision="a011"):
    """Refuse partial, nonempty, wrong-version or weakened day restore targets."""
    if revision not in {"a011", "a012"} or conn.execute("SELECT version_num FROM alembic_version").fetchall() != [(revision,)]:
        raise ValueError("restore target requires the exact declared revision")
    tables = ("activity_projection_day_sources", "activity_projection_days")
    if revision == "a012":
        tables += ("activity_course_buckets", "activity_course_days")
    for table in tables:
        from psycopg import sql
        if conn.execute(sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(table))).fetchone()[0] != 0:
            raise ValueError("day restore target is not empty")
    if conn.execute("SELECT run_id,phase,cursor_subject,cursor_bucket,cursor_metric FROM analytics.activity_projection_day_backfill").fetchall() != [(None, "scanning", None, None, None)]:
        raise ValueError("day restore backfill is not pristine")
    if conn.execute("SELECT run_id,phase,cursor_namespace,cursor_event_id FROM analytics.activity_projection_backfill").fetchall() != [(None, "scanning", None, None)]:
        raise ValueError("bucket restore backfill is not pristine")
    verify_day_trigger(conn, target)
    public_create = conn.execute("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n,
        LATERAL pg_catalog.aclexplode(coalesce(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
        WHERE n.nspname IN ('analytics','pg_catalog') AND a.grantee=0 AND a.privilege_type='CREATE')""").fetchone()[0]
    if public_create:
        raise ValueError("untrusted public CREATE on day trigger search path")
    for suffix in ("collector", "processor", "query", "privacy", "transfer_status"):
        role = target["name"] + "_" + suffix
        row = conn.execute("""SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin,rolinherit,
            pg_catalog.pg_has_role(rolname,%s,'MEMBER'),pg_catalog.has_schema_privilege(rolname,'analytics','CREATE'),
            pg_catalog.has_schema_privilege(rolname,'pg_catalog','CREATE'),
            EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=pg_catalog.pg_roles.oid)
            FROM pg_catalog.pg_roles WHERE rolname=%s""",
            (target["owner"], role)).fetchone()
        if row != (False, False, False, False, True, False, False, False, False, False):
            raise ValueError("unsafe day restore role")
    from .a011_acl import verify_day_privileges
    verify_day_privileges(conn, target)


def transplant_fresh(target, snapshot_data, *, day_projection=False, old_run_id=None, new_run_id=None,
                     day_revision="a011"):
    import psycopg
    from psycopg import sql
    browser_tables = ("processing_results", "processing_leases", "processing_replays", "admission_subject_windows", "activity_projection_buckets") if "activity_projection_buckets" in snapshot_data else ()
    if day_revision not in {"a011", "a012"} or (day_revision == "a012" and not day_projection):
        raise ValueError("unsupported day restore revision")
    if day_projection and not browser_tables:
        raise ValueError("day restore requires bucket snapshot")
    if day_projection:
        from uuid import UUID
        if not isinstance(old_run_id, UUID) or not isinstance(new_run_id, UUID) or old_run_id == new_run_id:
            raise ValueError("day restore requires two distinct known run identities")
        rows = snapshot_data.get("activity_projection_backfill", [])
        if len(rows) != 1:
            raise ValueError("day restore source backfill is unavailable")
        source = json.loads(rows[0])
        if source.get("run_id") != str(old_run_id) or source.get("phase") != "complete":
            raise ValueError("day restore source run is not complete")
    tables = TABLES + (("core_snapshot_proofs",) if "core_snapshot_proofs" in snapshot_data else ()) + browser_tables
    privacy_tables = ("subject_fences", "privacy_receipts") if "privacy_receipts" in snapshot_data else ()
    catalog_tables = ("catalog_versions",) if "catalog_versions" in snapshot_data else ()
    backfill = {"activity_projection_backfill"} if browser_tables else set()
    if set(snapshot_data) != set(tables) | {"privacy_state"} | set(privacy_tables) | set(catalog_tables) | backfill:
        raise ValueError("unexpected snapshot shape")
    with psycopg.connect(target["dsn"], connect_timeout=3, options="-c statement_timeout=3000 -c lock_timeout=1000") as conn:
        actual = conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        if actual != (target["name"], target["owner"]) or conn.execute("SELECT token FROM public.cross_service_sentinel").fetchall() != [(target["sentinel"],)]:
            raise ValueError("restore target guard failed")
        if day_projection:
            verify_fresh_a011_target(conn, target, revision=day_revision)
        for table in (*tables, "privacy_state", *privacy_tables, *catalog_tables):
            if conn.execute(sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(table))).fetchone()[0] != 0:
                raise ValueError("restore target is not empty")
        if browser_tables:
            if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]:
                raise ValueError("restore target has other sessions")
            trigger = conn.execute("""SELECT tgenabled FROM pg_trigger
                WHERE tgrelid='analytics.activity_facts'::regclass
                  AND tgname='activity_projection_fact_inserted' AND NOT tgisinternal""").fetchall()
            if trigger != [("O",)]:
                raise ValueError("unexpected projection trigger state")
            # A logical snapshot already contains the corresponding buckets.
            # Suppress only their insert trigger in this fresh target transaction;
            # rollback restores its prior state if any subsequent step fails.
            conn.execute("ALTER TABLE analytics.activity_facts DISABLE TRIGGER activity_projection_fact_inserted")
        for table in (*tables, "privacy_state", *privacy_tables, *catalog_tables):
            columns = [row[0] for row in conn.execute("""SELECT column_name FROM information_schema.columns
                WHERE table_schema='analytics' AND table_name=%s AND is_generated='NEVER' ORDER BY ordinal_position""", (table,))]
            if table == "activity_projection_buckets" and day_revision == "a012":
                # A freshly restored dataset regenerates its course sidecars.
                columns = [name for name in columns if name not in {"course_projection_generation", "course_projection_run_id"}]
            if not columns:
                raise ValueError("restore table has no writable columns")
            for row in snapshot_data[table]:
                # The source is this process's earlier guarded read, not an
                # arbitrary external backup. Insert into a new DB only.
                json.loads(row)
                names = sql.SQL(",").join(sql.Identifier(name) for name in columns)
                selections = sql.SQL(",").join(sql.SQL("x.{}").format(sql.Identifier(name)) for name in columns)
                conn.execute(sql.SQL("INSERT INTO analytics.{} ({}) SELECT {} FROM jsonb_populate_record(NULL::analytics.{}, %s::jsonb) AS x")
                             .format(sql.Identifier(table), names, selections, sql.Identifier(table)), (row,))
        if browser_tables:
            initial = conn.execute("SELECT run_id,phase,cursor_namespace,cursor_event_id FROM analytics.activity_projection_backfill WHERE singleton=true").fetchone()
            if initial != (None, "scanning", None, None):
                raise ValueError("restore target backfill is not fresh")
            rows = snapshot_data["activity_projection_backfill"]
            if len(rows) != 1:
                raise ValueError("invalid old projection backfill snapshot")
            conn.execute("""UPDATE analytics.activity_projection_backfill AS b SET
                run_id=x.run_id, phase=x.phase, cursor_namespace=x.cursor_namespace, cursor_event_id=x.cursor_event_id
                FROM jsonb_populate_record(NULL::analytics.activity_projection_backfill,%s::jsonb) AS x WHERE b.singleton=true""", (rows[0],))
            conn.execute("ALTER TABLE analytics.activity_facts ENABLE TRIGGER activity_projection_fact_inserted")
        if day_projection:
            # Only the bucket trigger may create day sources here. Day payloads
            # and the old day backfill are deliberately never transplanted.
            mismatch = conn.execute("""WITH expected AS (
                SELECT DISTINCT subject_ref,(bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date AS local_day,metric_version
                FROM analytics.activity_projection_buckets),
                observed AS (SELECT subject_ref,local_day,metric_version FROM analytics.activity_projection_day_sources)
                SELECT EXISTS(SELECT * FROM expected EXCEPT SELECT * FROM observed)
                    OR EXISTS(SELECT * FROM observed EXCEPT SELECT * FROM expected)""").fetchone()[0]
            if mismatch or conn.execute("SELECT count(*) FROM analytics.activity_projection_day_sources WHERE state<>'dirty'").fetchone()[0]:
                raise ValueError("bucket-to-day reconstruction mismatch")
            if conn.execute("SELECT count(*) FROM analytics.activity_projection_days").fetchone()[0]:
                raise ValueError("day payload appeared during restore")
    if browser_tables:
        with psycopg.connect(target["dsn"], connect_timeout=3, options="-c statement_timeout=3000 -c default_transaction_read_only=on") as conn:
            trigger = conn.execute("""SELECT tgenabled FROM pg_trigger
                WHERE tgrelid='analytics.activity_facts'::regclass
                  AND tgname='activity_projection_fact_inserted' AND NOT tgisinternal""").fetchall()
            if trigger != [("O",)]:
                raise ValueError("projection trigger was not enabled after restore commit")
            if day_projection:
                verify_day_trigger(conn, target)
