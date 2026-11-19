# Analytics PostgreSQL: synthetic foundation (CORP-63)

The initial `a001` migration creates one `analytics.activity_facts` table and
`analytics.subject_fences`. Facts carry the canonical v1 hash and retain the
`(producer_namespace, event_id)` identity; timestamps and course, catalog,
lesson and subject indexes are shared across courses. No per-course tables,
mandatory rollups, Core database links or Redis are created. A different hash
algorithm requires its own version and compatible migration.

The migrator owns the database/schema and is separate from the non-owner
Collector, Processor, Query and Privacy logins. Collector can insert and read
facts; Processor and Query can only read them; Privacy can delete, but none can
update payloads. These database grants do not implement grant verification,
subject authorization or deletion fencing. A subject-fence table is a reserved
primitive, not an implementation of the deletion or restore protocols. Job,
lease, result, catalog and trusted-progress tables are deferred until their
actual writers and transactions are implemented.

`ActivityStorage.insert` is a transaction-local primitive: it returns inserted,
duplicate or conflict using the contract's canonical hash (which excludes
`received_at`). Its result is not a durable ACK: the caller must atomically
validate privacy/catalog and create the required jobs before commit. CORP-78,
CORP-81 and CORP-427 own those missing algorithms and their failure tests.
Every operation uses its own async session/transaction; the configured pool,
overflow, connect, statement wait and transaction duration are bounded. Cancellation or exception
rolls back; an uncertain commit still requires reconciliation by identity.

The explicit `python -m pymentor_analytics.app.cli migrate` command consumes
`ANALYTICS_MIGRATE_DATABASE_URL`; HTTP processes never run migrations. The
migrator takes a bounded transaction advisory lock and runs Alembic `upgrade
head` in the same transaction. Concurrent invocations serialize, and repeat
upgrades are safe. Revision `a001` is forward-only: application rollback does
not drop tables or reverse data. Previous scaffold HTTP roles remain closed on
an empty or upgraded database; no previous Analytics schema existed to migrate.

Readiness deliberately stays 503 even when schema and DB are available. An
authenticated independent authority, full applied prefix, attached stream and
fresh applied opening barrier from `privacy_v1.RestoreProof` are not yet wired.
The schema diagnostic cannot open this barrier; restoring a database must never
turn a stored watermark into proof. The injection point for scaffold tests is not
an operational privacy adapter. No production data or Core integration is
authorized by this foundation.

For the isolated local synthetic PostgreSQL, `tools/test_postgres.py` loads the
ignored `.local/postgres-connection.json` created by the local operator. The
bootstrap accepts only the designated synthetic project, host, port, bootstrap
identity and database. Every test gets a fresh `analytics_test_<uuid>` database,
dedicated random-password roles and an independent sentinel; cleanup checks the
exact name, sentinel and owner before dropping that database and its roles. It
never connects to port 57382 or a Core database. If creation fails before the
sentinel is installed, automatic deletion is intentionally refused; reconcile
the identified disposable resources manually using the same safety boundary.

Actual checks on 16.11.2026 used PostgreSQL 16 on the isolated endpoint, Python
3.12.10 and SQLAlchemy 2.0.51, asyncpg 0.32.0, psycopg 3.3.8 and Alembic 1.20.1.
The tests cover empty/repeat/concurrent upgrade, a held migration lock, grants,
immutable payload, duplicate/conflict, privacy delete, rollback/cancellation,
pool exhaustion and closed readiness. The rebuilt `knowi-analytics:corp63-synthetic`
image also passed isolated liveness/readiness/shutdown checks for all three roles,
and its migrator upgraded a fresh synthetic database. The previous scaffold
image (`knowi-analytics:an1-local`) started as Query with that migrated DB URL,
returned live 200 and ready 503, and shut down cleanly. The scaffold does not
read stored facts, so this is only a process rollback smoke check, not data
consumer compatibility. No throughput, production security,
restore, live consumer, collector or processor guarantee follows from those
checks. Re-run from `services/analytics`:

```powershell
.\.venv\Scripts\python.exe tools/test_postgres.py
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/postgres -q -p no:cacheprovider
```

To include the optional image migration and scaffold rollback smoke checks,
build the local test image, retain the previous scaffold image, and set
`ANALYTICS_TEST_IMAGE=knowi-analytics:corp63-synthetic` for the first command.

Provisioning of real deployment roles, secret distribution, cross-database
connect restrictions on any shared server, resource budgets, and backup/restore
are outside this synthetic test; database separation must be verified where the
service is deployed. The dedicated test cluster has no Core DB; this does not
prove effective Core isolation on a different cluster.
