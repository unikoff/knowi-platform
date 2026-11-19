"""Exact a011 restore-target ACL checks, before any transplant side effect."""
TABLES = ("activity_projection_day_sources", "activity_projection_days", "activity_projection_day_backfill")
ROLES = ("collector", "processor", "query", "privacy", "transfer_status")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SOURCE_UPDATES = frozenset(("state", "claim_id", "owner", "lease_expires_at", "claim_generation", "claim_epoch",
                            "claim_run_id", "attempts", "next_attempt_at", "last_failure"))
BACKFILL_UPDATES = frozenset(("phase", "cursor_subject", "cursor_bucket", "cursor_metric"))
FUNCTIONS = {
    "activity_projection_bucket_changed": "analytics.activity_projection_bucket_changed()",
    "register_activity_day": "analytics.register_activity_day(uuid,timestamptz,text,uuid)",
    "begin_activity_day_backfill": "analytics.begin_activity_day_backfill(uuid)",
    "invalidate_stale_activity_day": "analytics.invalidate_stale_activity_day(uuid,date,uuid)",
}


def table_permissions(role, table):
    if role == "processor":
        return {"SELECT", "INSERT", "UPDATE"} if table == "activity_projection_days" else {"SELECT"}
    if role == "query":
        return {"SELECT"}
    if role == "privacy" and table != "activity_projection_day_backfill":
        return {"SELECT", "DELETE"}
    return set()


def column_permissions(role, table, column):
    tables = table_permissions(role, table)
    result = tables & set(COL_PRIVILEGES)
    if role == "transfer_status" and table != "activity_projection_day_backfill" and column == "subject_ref":
        result.add("SELECT")
    if role == "processor" and ((table == "activity_projection_day_sources" and column in SOURCE_UPDATES)
                               or (table == "activity_projection_day_backfill" and column in BACKFILL_UPDATES)):
        result.add("UPDATE")
    return result


def verify_day_privileges(conn, target):
    names = [target["name"] + "_" + role for role in ROLES]
    suffix = {target["name"] + "_" + role: role for role in ROLES}
    rows = conn.execute("""SELECT c.relname,pg_get_userbyid(c.relowner),
        EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee=0),
        EXISTS(SELECT 1 FROM pg_catalog.pg_attribute x CROSS JOIN LATERAL pg_catalog.aclexplode(x.attacl) a
               WHERE x.attrelid=c.oid AND x.attnum>0 AND NOT x.attisdropped AND a.grantee=0)
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='analytics' AND c.relkind='r' AND c.relname=ANY(%s)""", (list(TABLES),)).fetchall()
    if len(rows) != len(TABLES) or {row[0] for row in rows} != set(TABLES) or any(row[1:] != (target["owner"], False, False) for row in rows):
        raise ValueError("unsafe day table owner or PUBLIC privileges")
    rows = conn.execute("""SELECT c.relname,r.rolname,
        has_table_privilege(r.oid,c.oid,'SELECT'),has_table_privilege(r.oid,c.oid,'INSERT'),
        has_table_privilege(r.oid,c.oid,'UPDATE'),has_table_privilege(r.oid,c.oid,'DELETE'),
        has_table_privilege(r.oid,c.oid,'TRUNCATE'),has_table_privilege(r.oid,c.oid,'REFERENCES'),
        has_table_privilege(r.oid,c.oid,'TRIGGER')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN pg_catalog.pg_roles r WHERE n.nspname='analytics' AND c.relkind='r'
        AND c.relname=ANY(%s) AND r.rolname=ANY(%s)""", (list(TABLES), names)).fetchall()
    seen = set()
    for table, role, *actual in rows:
        pair = (table, role)
        if table not in TABLES or role not in suffix or pair in seen:
            raise ValueError("ambiguous day table privileges")
        seen.add(pair)
        expected = table_permissions(suffix[role], table)
        if tuple(actual) != tuple(priv in expected for priv in PRIVILEGES):
            raise ValueError("unsafe or missing day table privilege")
    if len(seen) != len(TABLES) * len(ROLES):
        raise ValueError("incomplete day table privileges")
    rows = conn.execute("""SELECT c.relname,r.rolname,a.attname,
        has_column_privilege(r.oid,c.oid,a.attnum,'SELECT'),has_column_privilege(r.oid,c.oid,a.attnum,'INSERT'),
        has_column_privilege(r.oid,c.oid,a.attnum,'UPDATE'),has_column_privilege(r.oid,c.oid,a.attnum,'REFERENCES')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        CROSS JOIN pg_catalog.pg_roles r WHERE n.nspname='analytics' AND c.relkind='r'
        AND c.relname=ANY(%s) AND r.rolname=ANY(%s)""", (list(TABLES), names)).fetchall()
    columns = {table: set() for table in TABLES}
    observed = set()
    for table, role, column, *actual in rows:
        key = (table, role, column)
        if table not in columns or role not in suffix or key in observed:
            raise ValueError("ambiguous day column privileges")
        observed.add(key)
        columns[table].add(column)
        expected = column_permissions(suffix[role], table, column)
        if tuple(actual) != tuple(priv in expected for priv in COL_PRIVILEGES):
            raise ValueError("unsafe or missing day column privilege")
    if not (SOURCE_UPDATES | {"subject_ref"}) <= columns[TABLES[0]] or not {"subject_ref"} <= columns[TABLES[1]] or not BACKFILL_UPDATES <= columns[TABLES[2]]:
        raise ValueError("incomplete day table columns")
    if any((table, name, column) not in observed for table, cols in columns.items() for name in names for column in cols):
        raise ValueError("incomplete day column privilege matrix")
    signatures = list(FUNCTIONS.values())
    rows = conn.execute(r"""SELECT p.proname,pg_get_userbyid(p.proowner),p.prosecdef,
        EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
               WHERE a.grantee=0 AND a.privilege_type='EXECUTE'),
        EXISTS(SELECT 1 FROM unnest(coalesce(p.proconfig,ARRAY[]::text[])) cfg(setting)
               WHERE regexp_replace(cfg.setting,'\s+','','g')='search_path=pg_catalog,analytics')
        FROM pg_catalog.pg_proc p WHERE p.oid IN
          (to_regprocedure(%s),to_regprocedure(%s),to_regprocedure(%s),to_regprocedure(%s))""", tuple(signatures)).fetchall()
    if len(rows) != len(FUNCTIONS) or {row[0] for row in rows} != set(FUNCTIONS) or any(row[1:] != (target["owner"], True, False, True) for row in rows):
        raise ValueError("unsafe or missing day function")
    rows = conn.execute("""SELECT p.proname,r.rolname,has_function_privilege(r.oid,p.oid,'EXECUTE')
        FROM pg_catalog.pg_proc p CROSS JOIN pg_catalog.pg_roles r WHERE p.oid IN
          (to_regprocedure(%s),to_regprocedure(%s),to_regprocedure(%s),to_regprocedure(%s))
          AND r.rolname=ANY(%s)""", (*signatures, names)).fetchall()
    seen = set()
    for function, role, actual in rows:
        pair = (function, role)
        if function not in FUNCTIONS or role not in suffix or pair in seen:
            raise ValueError("ambiguous day function privileges")
        seen.add(pair)
        expected = suffix[role] == "processor" and function != "activity_projection_bucket_changed"
        if actual is not expected:
            raise ValueError("unsafe or missing day function privilege")
    if len(seen) != len(FUNCTIONS) * len(ROLES):
        raise ValueError("incomplete day function privileges")
