# Analytics

Independent modular backend, currently synthetic-only. Collector, Processor and
Query are separate commands. Separate PostgreSQL schema, role permissions,
explicit migrator, signed grants, HTTP intake, atomic facts/jobs, clock metadata
and shared admission budgets are implemented. Processor leases and synthetic
handlers, bounded replay and the authenticated synthetic deletion protocol are
implemented. Infrastructure acceptance remains incomplete.
See [execution](../../docs/analytics/EXECUTION.md),
[architecture](../../docs/analytics/architecture.md) and
[PostgreSQL checks](../../docs/analytics/postgres.md).

From this directory, install `requirements-dev.txt` in a Python 3.12 environment.
Use the guarded [local runbook](../../docs/analytics/run-local.md) to create
synthetic credentials, initialize the separate database and start the stack.
Each role uses its own `ANALYTICS_<ROLE>_DATABASE_URL`. Never use Core's
database. Standalone command: `python -m pymentor_analytics.app.cli collector --port 8001`
(also `processor`, `query`, `privacy`, or `migrate`). Migration requires its separate owner
role and takes an advisory lock. The local upgrade tool pins an explicit revision;
HTTP startup does not migrate. `/live` returns200;
`/ready` defaults to503. A configured synthetic authority opens it only after a
fresh verified barrier and matching applied deletion prefix; see the
[privacy runtime](../../docs/analytics/privacy-runtime.md).
No real telemetry may be enabled. Never use retained Core credentials or data.

Successful signed HTTP→PostgreSQL checks use explicit synthetic readiness,
catalog and early-fence implementations. The commit fence is a real DB check;
that test is not a complete privacy/restore or production acceptance. The separate
`tools/test_privacy_integration.py` exercises the signed authority and real PG
gate, including an old logical snapshot. Encrypted backup recovery is separate.

Run `python -B -m pytest -p no:cacheprovider tests` for non-opted-in checks.
`tools/test_postgres.py` explicitly opts into the designated synthetic cluster,
creates a guarded UUID database and cleans only that database. Image health:
`python -B tools/check_image.py <image>`.

The separate Analytics CI checks source/contracts/image/Helm and never publishes
images or deploys. Live PostgreSQL proof currently uses the guarded local runner;
its absence in CI is reported as skipped tests, not a database PASS.
