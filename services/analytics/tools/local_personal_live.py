"""Prepare one persistent, local-only personal Analytics instance.

Default prints an inert plan. --apply is one-shot: partial external outcomes
remain recorded for reconciliation; no database, role or journal is deleted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import quote
from uuid import UUID, uuid4

SERVICE = Path(__file__).resolve().parents[1]
WORKSPACE = SERVICE.parents[1]
CORE = WORKSPACE / "services/core-api"
if str(SERVICE) not in sys.path: sys.path.insert(0, str(SERVICE))
if str(SERVICE / "tools") not in sys.path: sys.path.insert(0, str(SERVICE / "tools"))
import local_personal_runtime

ROLES = ("migrate", "collector", "processor", "query", "privacy", "transfer_status")
ANALYTICS_ID = "693d9afb4c4270438dd8130064a938e1611d61bcbf0641654ab03442f30f85c4"
CORE_ID = "cb0ddbbd527440655f472e6ca9a8a7ee19438b2f30ca8e75ffd98d04d84e8b87"
CORE_VOLUME = "knowi-ra-01a1df75_role_admin_postgres"
_PHASE = "arguments"


def _phase(value):
    global _PHASE
    _PHASE = value


# A fresh DirectorySecurity object is essential: editing an inherited ACL in
# place would retain unrelated explicit ACEs. This command is only used on a
# directory this process just created, before private bytes are written.
WINDOWS_PRIVATE_ACL = r'''
$ErrorActionPreference = 'Stop'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($user)
$acl.SetAccessRuleProtection($true, $false)
$inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
foreach ($sid in @($user, $system)) {
  $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid, [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit, [System.Security.AccessControl.PropagationFlags]::None, [System.Security.AccessControl.AccessControlType]::Allow)
  $acl.AddAccessRule($rule)
}
[System.IO.Directory]::SetAccessControl($env:ANALYTICS_NEW_PRIVATE_DIRECTORY, $acl)
'''


def _set_new_windows_acl(path):
    root = Path(os.environ["SystemRoot"])
    if not root.is_absolute():
        raise ValueError("Windows system path unavailable")
    shell = root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", WINDOWS_PRIVATE_ACL],
                   env={**os.environ, "ANALYTICS_NEW_PRIVATE_DIRECTORY": str(path)},
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def instance_uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError("invalid local instance")
    return value


def database_name(value):
    result = "analytics_live_" + UUID(instance_uuid(value)).hex
    if len(result + "_transfer_status") > 63:
        raise ValueError("database role identity too long")
    return result


def plan():
    return {"status": "UNRUN", "action": "plan", "database_prefix": "analytics_live_",
            "persistent": True, "automatic_deletion": False, "roles": list(ROLES),
            "ports": {"collector": 18042, "query": 18043, "processor": 18044, "privacy": 18045, "authority": 18046}}


def _write(local, folder, name, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    local._write_new(local._file(folder, name, required=False), raw)
    local._private(local._file(folder, name))


def _private_directory(path, local):
    if path.exists() or path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("local private directory already exists")
    path.mkdir(mode=0o700)
    if os.name == "nt":
        _set_new_windows_acl(path)
    else:
        os.chmod(path, 0o700)
    local._private(path, directory=True)


def prepare_folder(instance_id):
    """Create ACL-restricted parent and child before any secret bytes."""
    from pymentor_analytics.adapters.privacy_authority import local_server as local
    instance_id = instance_uuid(instance_id)
    service = SERVICE.resolve(strict=True)
    base = service / ".local"
    if base.is_symlink() or base.is_junction() or not base.is_dir() or base.resolve(strict=True) != base:
        raise ValueError("unsafe local base")
    root = base / "local-live"
    _phase("private-parent")
    if root.exists(): local._private(root, directory=True)
    else: _private_directory(root, local)
    folder = root / instance_id
    _phase("private-instance")
    _private_directory(folder, local)
    core_id, run_id = str(uuid4()), str(uuid4())
    if len({instance_id, core_id, run_id}) != 3:
        raise ValueError("local identities must be distinct")
    owner = {"version": 1, "mode": "local", "do_not_drop": True, "instance_id": instance_id,
             "core_instance_id": core_id, "dataset_run_id": run_id, "authority_id": "local-authority:" + instance_id}
    _write(local, folder, "owner.json", owner)
    return local, folder, owner


def _pem_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    result = {}
    for name in ("g1", "p1", "authority", "command"):
        key = Ed25519PrivateKey.generate()
        result[name] = (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()),
                        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
                        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    if len({value[2] for value in result.values()}) != 4:
        raise ValueError("key separation failed")
    return result


def _secret32():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def build_configuration(folder, owner, keys, reader, source_secret, passwords, core_database, *, projection_revision="a011"):
    """Build isolated in-memory settings; callers write only into the private root."""
    if projection_revision not in local_personal_runtime.PROJECTION_REVISIONS:
        raise ValueError("invalid local projection revision")
    use_course_projection = projection_revision != "a011"
    instance, core_id, run_id = (owner[k] for k in ("instance_id", "core_instance_id", "dataset_run_id"))
    name = database_name(instance)
    if set(passwords) != set(ROLES) or len(set(passwords.values())) != len(ROLES):
        raise ValueError("role credentials must be independent")
    pub = {key: value[1].decode("ascii") for key, value in keys.items()}
    issuer, producer_issuer = "local-grants:" + instance, "local-producer:" + instance
    namespace = "core-local:" + core_id
    manifest = {"grant": {"issuer": issuer, "keys": {"g1": pub["g1"]}},
                "producer": {"issuer": producer_issuer, "keys": {"p1": pub["p1"]}, "allowlist": ["core-local"]}}
    authority = {"url": "http://127.0.0.1:18046", "authority_id": owner["authority_id"], "issuer": owner["authority_id"],
                 "public_keys": {"a1": pub["authority"]}, "reader_secret": reader, "fixture_mode": False, "runtime_mode": "local"}
    source = {"url": "http://127.0.0.1:18041/api/internal/analytics/deletions", "instance_id": core_id, "secret": source_secret}
    relay = {"producer": "core-local", "producer_issuer": producer_issuer, "producer_kid": "p1",
             "producer_private_key_pem": keys["p1"][0].decode("ascii"), "command_private_key_pem": keys["command"][0].decode("ascii")}
    authority_server = {"mode": "local", "fixture_mode": False, "bind_host": "127.0.0.1", "port": 18046,
        "issuer": owner["authority_id"], "kid": "a1", "signing_key_file": "authority.private.pem",
        "producer_issuer": producer_issuer, "producer_public_keys": {"p1": pub["p1"]}, "allowed_producers": ["core-local"],
        "producer_command_public_keys": {"core-local": pub["command"]}, "reader_secret_file": "authority.reader"}
    urls = {role: "postgresql://" + name + "_" + role + ":" + quote(passwords[role], safe="") + "@127.0.0.1:57392/" + name for role in ROLES}
    shared = {"real_collection": True, "issuer_mode": "local", "pool_size": 1, "pool_overflow": 0,
              "trust_manifest_json": json.dumps(manifest), "privacy_authority_json": json.dumps(authority),
              "core_deletion_source_json": json.dumps(source)}
    roles = {role: {**shared, "database_url": urls[role]} for role in ("collector", "query", "processor", "privacy")}
    for role in ("collector", "query"): roles[role]["core_deletion_status_database_url"] = urls["transfer_status"]
    roles["collector"].update(catalog_enabled=True, catalog_namespace=namespace,
                              core_transfer_namespaces_json=json.dumps({"core-local": namespace}))
    roles["processor"].update(activity_projection_enabled=True, activity_projection_run_id=run_id,
                              activity_day_worker_enabled=True, activity_course_worker_enabled=use_course_projection,
                              activity_boundary_credit_enabled=projection_revision in {"a013", "a014"},
                              processor_bucket_worker_enabled=True,
                              processor_generic_worker_enabled=False)
    roles["query"].update(activity_projection_enabled=True, activity_projection_run_id=run_id,
                          activity_day_query_enabled=True, activity_course_query_enabled=use_course_projection)
    roles["privacy"]["core_deletion_relay_json"] = json.dumps(relay)
    fingerprint = hashlib.sha256(keys["p1"][2]).hexdigest()
    core_env = {"PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "1", "PYMENTOR_ANALYTICS_GRANTS_ENABLED": "1",
      "PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED": "1", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "0",
      "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED": "0", "PYMENTOR_ANALYTICS_LEGACY_READ_MODE": "off",
      "PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE": "mirror", "PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED": "0",
      "PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE": core_database, "PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID": core_id,
      "PYMENTOR_ANALYTICS_LOCAL_DATASET_ID": instance, "PYMENTOR_ANALYTICS_LOCAL_SOURCE_KEY_FILE": str(folder / "source.key"),
      "PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE": namespace, "PYMENTOR_ANALYTICS_LOCAL_CATALOG_URL": "http://127.0.0.1:18042",
      "PYMENTOR_ANALYTICS_LOCAL_CATALOG_PRODUCER_KEY_SHA256": fingerprint, "PYMENTOR_ANALYTICS_PRODUCER_ID": "core-local",
      "PYMENTOR_ANALYTICS_PRODUCER_ISSUER": producer_issuer, "PYMENTOR_ANALYTICS_PRODUCER_KID": "p1",
      "PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE": str(folder / "p1.private.pem"), "PYMENTOR_ANALYTICS_GRANT_ISSUER": issuer,
      "PYMENTOR_ANALYTICS_GRANT_KID": "g1", "PYMENTOR_ANALYTICS_GRANT_KEY_FILE": str(folder / "g1.private.pem")}
    return {"authority_server": authority_server, "roles": {"version": 1, "instance_id": instance, "roles": roles},
            "core_env": core_env, "urls": urls, "shared": shared}


def _docker_target_preflight():
    """Read-only exact container/volume checks; never start or relabel anything."""
    import resource_snapshot as rs
    import run_k3s
    context = rs.local_context(dict(os.environ))
    prefix = ["docker", "--context", context]
    analytics = json.loads(run_k3s.run(prefix + ["inspect", "--type", "container", ANALYTICS_ID]).stdout)
    aid, cap, _ = rs.verify_container(analytics)
    rs.verify_volume(json.loads(run_k3s.run(prefix + ["volume", "inspect", rs.VOLUME]).stdout))
    core = json.loads(run_k3s.run(prefix + ["inspect", "--type", "container", CORE_ID]).stdout)
    core_volume = json.loads(run_k3s.run(prefix + ["volume", "inspect", CORE_VOLUME]).stdout)
    postgres_image = "sha256:63e83b5a3dc36a917dccb76fe1bd2ac51f1ec9c1deb47ff20d4f04233bf6eae9"
    if (aid != ANALYTICS_ID or cap != 384 * 1024**2 or analytics[0]["HostConfig"].get("CgroupnsMode") != "private"
            or analytics[0]["Image"] != postgres_image or analytics[0]["HostConfig"].get("NanoCpus") != 500_000_000
            or analytics[0]["State"].get("Health", {}).get("Status") != "healthy"
            or len(core) != 1 or core[0]["Id"] != CORE_ID or core[0]["State"].get("Running") is not True
            or core[0]["Image"] != postgres_image or core[0]["State"].get("Health", {}).get("Status") != "healthy"
            or core[0]["HostConfig"].get("Memory") != 512 * 1024**2
            or core[0]["HostConfig"].get("CgroupnsMode") != "private"
            or len(core_volume) != 1 or core_volume[0].get("Name") != CORE_VOLUME or core_volume[0].get("Driver") != "local"
            or len([m for m in core[0]["Mounts"] if m.get("Type") == "volume" and m.get("Name") == CORE_VOLUME
                    and m.get("Destination") == "/var/lib/postgresql/data"]) != 1
            or core[0]["HostConfig"].get("PortBindings", {}).get("5432/tcp") != [{"HostIp": "127.0.0.1", "HostPort": "57382"}]):
        raise ValueError("local database container identity unavailable")


CORE_PREFLIGHT = r'''
import json,re,sys
from pathlib import Path
from uuid import uuid4
from sqlalchemy.engine import make_url
from pymentor_core.modules.analytics_delivery.local_collection import LocalCollection,read_snapshot
base=Path.cwd()/'.local/role-admin'
for p in (base,base/'browser-dataset.json',base/'run_api.ps1'):
    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()): raise ValueError('unsafe metadata')
meta=json.loads((base/'browser-dataset.json').read_text(encoding='utf-8'))
if meta.get('do_not_drop') is not True or meta.get('durable_postgres') is not True or meta.get('schema_revision')!='c528_course_leaders': raise ValueError('unsafe Core metadata')
name=meta['database']
if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,63}',name): raise ValueError('unsafe Core database')
matches=re.findall(r"\$env:DATABASE_URL\s*=\s*'([^']+)'",(base/'run_api.ps1').read_text(encoding='utf-8'))
if len(matches)!=1: raise ValueError('Core URL unavailable')
url=matches[0].rstrip('/')+'/'+name
parsed=make_url(url)
if parsed.drivername!='postgresql+psycopg' or parsed.host!='127.0.0.1' or parsed.port!=57382 or parsed.database!=name or parsed.query: raise ValueError('Core URL identity mismatch')
read_snapshot(LocalCollection(str(uuid4()),name,url,b'0'*32))
print(json.dumps({'database':name,'schema_revision':'c528_course_leaders','primary':True,'trigger_verified':True}))
'''


def _core_preflight():
    executable = CORE / ".venv/Scripts/python.exe"
    if not executable.is_file() or executable.is_symlink() or executable.is_junction():
        raise ValueError("Core Python unavailable")
    try:
        result = subprocess.run([str(executable), "-B", "-c", CORE_PREFLIGHT], cwd=CORE,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=15, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or len(result.stdout) > 4096: raise ValueError
        value = json.loads(result.stdout)
        if (set(value) != {"database", "schema_revision", "primary", "trigger_verified"}
                or value["schema_revision"] != "c528_course_leaders" or value["primary"] is not True
                or value["trigger_verified"] is not True or not re.fullmatch(r"[A-Za-z0-9_]{1,63}", value["database"])):
            raise ValueError
        return value["database"]
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        raise ValueError("Core read-only preflight unavailable") from None


def _admin_connection():
    import psycopg
    import bootstrap_postgres as bootstrap
    import bootstrap_runtime
    config = bootstrap.config(bootstrap_runtime.CONNECTION)
    return psycopg.connect(host=config["host"], port=config["port"], user=config["user"], password=config["password"],
                           dbname=config["database"], connect_timeout=5, autocommit=True,
                           options="-c statement_timeout=5000 -c lock_timeout=1000")


def _verify_admin(connection):
    row = connection.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int,pg_is_in_recovery()").fetchone()
    if row is None or row[:2] != ("analytics_bootstrap", "analytics_bootstrap") or not 160000 <= row[2] < 170000 or row[3] is not False:
        raise ValueError("Analytics primary identity unavailable")


def _pending(local, folder, name, target, action, verify):
    """One attempt after fsynced intent; no retry if action or verification fails."""
    _phase(name)
    intent = {"operation": name, "target": target}
    _write(local, folder, name + ".pending.json", intent)
    action()
    if verify() is not True:
        raise ValueError("operation outcome unknown")
    _write(local, folder, name + ".confirmed.json", intent)


def _password_verifier(connection, role, password):
    """Generate a PostgreSQL SCRAM verifier locally, never a plaintext SQL literal."""
    if not isinstance(password, str) or not password or not isinstance(role, str):
        raise ValueError("invalid role credential")
    verifier = connection.pgconn.encrypt_password(password.encode("utf-8"), role.encode("ascii"), b"scram-sha-256").decode("ascii")
    if not verifier.startswith("SCRAM-SHA-256$"):
        raise ValueError("role password verifier unavailable")
    return verifier


def _write_config_files(local, folder, owner, keys, reader, source_secret, passwords, config):
    for name, (private, public, _) in keys.items():
        _write(local, folder, name + ".private.pem", private)
        _write(local, folder, name + ".public.pem", public)
    _write(local, folder, "authority.reader", reader.encode("ascii"))
    _write(local, folder, "source.key", source_secret.encode("ascii"))
    _write(local, folder, "credentials.json", {"database": database_name(owner["instance_id"]), "passwords": passwords})
    _write(local, folder, "authority-server.json", config["authority_server"])
    _write(local, folder, "role-settings.json", config["roles"])
    _write(local, folder, "core-env.json", config["core_env"])


def apply(instance_id, *, projection_revision="a011"):
    """One attempt for a fresh instance. Any partial state requires reconciliation."""
    from psycopg import sql
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.adapters.postgres.migrate import upgrade
    import bootstrap_runtime
    instance_id = instance_uuid(instance_id)
    if projection_revision not in local_personal_runtime.PROJECTION_REVISIONS:
        raise ValueError("invalid local projection revision")
    name = database_name(instance_id)
    _phase("container-preflight")
    _docker_target_preflight()
    _phase("core-preflight")
    core_database = _core_preflight()
    _phase("admin-preflight")
    with _admin_connection() as conn:
        _verify_admin(conn)
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is not None:
            raise ValueError("persistent database already exists; reconcile")
        role_names = [name + "_" + role for role in ROLES]
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s) LIMIT 1", (role_names,)).fetchone() is not None:
            raise ValueError("persistent role already exists; reconcile")
        history = bootstrap_runtime.schema_history(projection_revision)
        if history[0] != projection_revision or len(history[1]) != int(projection_revision[1:]):
            raise ValueError("unexpected Analytics migration history")
        local, folder, owner = prepare_folder(instance_id)
        _phase("private-configuration")
        keys, reader, source_secret = _pem_keys(), _secret32(), _secret32()
        if reader == source_secret: raise ValueError("secret separation failed")
        passwords = {role: secrets.token_urlsafe(32) for role in ROLES}
        config = build_configuration(folder, owner, keys, reader, source_secret, passwords, core_database,
                                     projection_revision=projection_revision)
        _write_config_files(local, folder, owner, keys, reader, source_secret, passwords, config)
        # Re-read and validate role isolation before the first external mutation.
        local_personal_runtime.read_settings(instance_id, require_prepared=False, projection_revision=projection_revision)
        _write(local, folder.parent, "protected-" + UUID(instance_id).hex + ".json",
               {"version": 1, "instance_id": instance_id, "database": name, "do_not_drop": True})
        for role in ROLES:
            target = name + "_" + role
            # libpq computes SCRAM locally; SQL/logs never receive plaintext.
            verifier = _password_verifier(conn, target, passwords[role])
            _pending(local, folder, "role-" + role, target,
                lambda target=target, verifier=verifier: conn.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS")
                    .format(sql.Identifier(target), sql.Literal(verifier))),
                lambda target=target: conn.execute("""SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
                    AND NOT rolcreaterole AND NOT rolinherit AND NOT rolbypassrls AND NOT rolreplication
                    AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=p.oid)
                    FROM pg_roles p WHERE rolname=%s""", (target,)).fetchone() == (True,))
        _pending(local, folder, "database-create", name,
            lambda: conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(name + "_migrate"))),
            lambda: conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)).fetchone() == (name + "_migrate",))
        _pending(local, folder, "database-revoke-public", name,
            lambda: conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))),
            lambda: conn.execute("""SELECT NOT EXISTS (SELECT 1 FROM pg_database d,
                LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
                WHERE d.datname=%s AND a.grantee=0 AND a.privilege_type IN ('CONNECT','CREATE','TEMPORARY'))""", (name,)).fetchone() == (True,))
        for role in ROLES:
            target = name + "_" + role
            _pending(local, folder, "database-grant-" + role, target,
                lambda target=target: conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(target))),
                lambda target=target: conn.execute("SELECT has_database_privilege(%s,%s,'CONNECT')", (target, name)).fetchone() == (True,))
        for role in ROLES:
            _verify_role_login(config["urls"][role], name, name + "_" + role)
        migrate_settings = Settings.model_validate({**config["shared"], "database_url": config["urls"]["migrate"]})
        _pending(local, folder, "schema-" + projection_revision, name,
                 lambda: upgrade(migrate_settings, target_revision=projection_revision),
                 lambda: _db_revision(config["urls"]["migrate"]) == projection_revision)
        _pending(local, folder, "projection-begin", name,
                 lambda: _begin_projection(config["urls"]["migrate"], owner["dataset_run_id"]),
                 lambda: _projection_started(config["urls"]["migrate"], owner["dataset_run_id"]))
        _pending(local, folder, "authority-journal-init", owner["authority_id"],
                 lambda: local.initialize(instance_id), lambda: local._file(folder, local.JOURNAL_FILE).is_file())
        _write(local, folder, "prepared.json", {"version": 1, "instance_id": instance_id, "database": name,
               "schema_revision": projection_revision, "history_sha256": history[2],
               "backfill": "NOT_YET_VERIFIED", "do_not_drop": True})
        return {"status": "prepared_backfill_unverified", "instance_id": instance_id, "database": name}


def _db_revision(url):
    import psycopg
    with psycopg.connect(url, connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        rows = conn.execute("SELECT version_num FROM public.alembic_version").fetchall()
        return rows[0][0] if len(rows) == 1 else None


def _verify_role_login(url, database, role):
    import psycopg
    with psycopg.connect(url, connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        if conn.execute("SELECT current_database(),current_user,pg_is_in_recovery()").fetchone() != (database, role, False):
            raise ValueError("persistent role login identity mismatch")


def _begin_projection(url, run_id):
    import psycopg
    with psycopg.connect(url, connect_timeout=5, options="-c statement_timeout=5000 -c lock_timeout=1000") as conn:
        if conn.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() != (0,):
            raise ValueError("new database contains unexpected facts")
        conn.execute("SELECT analytics.begin_activity_projection_backfill(NULL,%s)", (UUID(run_id),))


def _projection_started(url, run_id):
    import psycopg
    with psycopg.connect(url, connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        return conn.execute("SELECT run_id=%s AND phase='scanning' FROM analytics.activity_projection_backfill WHERE singleton=true", (UUID(run_id),)).fetchone() == (True,)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id")
    parser.add_argument("--projection-revision", choices=local_personal_runtime.PROJECTION_REVISIONS,
                        help="schema and projection mode for a fresh --apply (default: a011)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
    if args.projection_revision is not None and not args.apply:
        parser.error("--projection-revision requires --apply")
    if not (args.apply or args.status or args.stop):
        print(json.dumps(plan(), sort_keys=True)); return 0
    _phase("arguments")
    try:
        instance = instance_uuid(args.instance_id)
        if args.apply:
            print(json.dumps(apply(instance, projection_revision=args.projection_revision or "a011"), sort_keys=True))
        else:
            return local_personal_runtime.main(["--instance-id", instance, "--status" if args.status else "--stop"])
        return 0
    except Exception:
        print(f"LOCAL_LIVE_OPERATION_FAILED phase={_PHASE}; preserve owned state and reconcile.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
