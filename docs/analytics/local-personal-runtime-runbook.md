# Local personal Analytics: operator runbook

This is a persistent local learner dataset, distinct from synthetic benchmarks
and retained Analytics datasets. Source and offline tests alone do not establish
that this runtime works. Runtime creation and startup require the separately
authorized local operation. The tools do not start or modify Core, Redis, Vite,
or an existing dataset. They never automatically delete the new dataset, roles,
authority journal, or facts.

From `services/analytics` in PowerShell, a no-I/O plan is:

```powershell
.\.venv\Scripts\python.exe -B tools\local_personal_live.py
.\.venv\Scripts\python.exe -B tools\local_personal_runtime.py
```

For a fresh canonical UUID, the operator can perform one preparation attempt:

```powershell
$instance = '<new-canonical-uuid>'
.\.venv\Scripts\python.exe -B tools\local_personal_live.py --instance-id $instance --apply
```

Preparation verifies the explicitly identified local Analytics and Core
Postgres containers, the protected Core metadata and a read-only Core snapshot.
It creates an ACL-protected `services/analytics/.local/local-live/<uuid>/`,
`analytics_live_<uuid-without-hyphens>`, six independently credentialed roles,
the a011 schema, an actual a010 backfill request and a new authority journal.
The private `protected-<uuid-without-hyphens>.json` registry marks the database
do-not-drop. The instance contains `owner.json`, private role settings,
`authority-server.json`, the six independent key materials, and `core-env.json`.
Never print or publish these files. `source.key` is the Core HMAC source key;
the Core instance UUID and the projection run UUID are distinct from the dataset
UUID. Preparation does not declare either backfill or service readiness complete.

Each external mutation has a durable pending record before its single attempt
and a confirmed record only after verification. If preparation fails or its
outcome is unknown, retain all files, pending records, roles, database and
journal. Reconcile the exact operation and target before any further action;
do not rerun `--apply` with that UUID and do not drop partial state. File fsync
is used; directory-entry persistence across Windows power loss is not claimed.

For instance `1742f31e-be55-40fe-8e76-d0d98adf2d65`, the first cold attempt
stopped at the empty parent directory's privacy check. Read-only reconciliation
found no instance, keys, roles, database or migration. The operator explicitly
repaired the ACL on that known empty parent. The second attempt stopped because
the private writer was missing, leaving only an empty instance directory and no
keys or database; after read-only reconciliation, the operator removed that
empty directory. The private writer was added and its focused suite passed
19 tests. The third preparation succeeded, with 17 pending and corresponding
confirmed records, the database, six roles, a011 schema and authority journal
retained. Existing directories are never resecured by the preparation tool.

On failure, the CLI prints a fixed `LOCAL_LIVE_OPERATION_FAILED` code and safe
phase, without exception details. A phase alone does not establish whether its
side effect occurred; reconcile the pending and actual state.

For a fresh instance, `--apply --projection-revision a012` provisions course
rollups in addition to the existing day projections. `a013` also enables
boundary credits in Processor. `a014` adds storage for raw browser tool intervals.
Omit the option for `a011`. The runtime checks
the saved revision, migration history and matching role flags on startup.
Choose a fresh instance ID for a different revision. The provisioning command
refuses an existing database or role.

After review of the resulting private configuration, the operator starts Core
with the appropriate allowlisted values from `core-env.json`, preserving the
existing Core DB and Redis. `PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE` is for the
separate publisher, not a reason to expose its key to Core. Then start the
authority and foreground four-role runtime as separate owned processes:

```powershell
.\.venv\Scripts\python.exe -B -m pymentor_analytics.adapters.privacy_authority.local_server serve --instance-id $instance
.\.venv\Scripts\python.exe -B tools\local_personal_runtime.py --instance-id $instance --serve
```

These are separate foreground commands; an operator starting both on Windows
must launch each in its own hidden process. The journal was initialized during
successful preparation; do not call `init` again. The collector, query,
processor, privacy and authority listen only on 127.0.0.1 ports 18042 through
18046 respectively. The four application roles use separate settings and role
credentials with pool size one and no overflow; the processor uses the actual
bucket and day workers, not the generic synthetic worker.

```powershell
.\.venv\Scripts\python.exe -B tools\local_personal_runtime.py --instance-id $instance --status
.\.venv\Scripts\python.exe -B tools\local_personal_runtime.py --instance-id $instance --stop
```

Status only reports ready after checking the process identity, owned listeners
and all four role readiness endpoints, including the source and projection
readiness enforced by the applications. Complete Core/source clearance, both
projection backfills and an actual learner flow must be verified before calling
the integration ready or starting a learner-facing session. Stop requests a
cooperative shutdown of the four-role process; stop the separately owned
authority process through its own supervised process identity. A stale marker
or ambiguous shutdown requires reconciliation; never kill an unverified PID.
Stopping does not delete any persistent data.

## Observed local run

For the instance above, the four Analytics roles reported ready with their
source and backfill checks. Core on 18041, authority on 18046 and the web app
on 5173 were running. Tracker, reports and personal flags were enabled; cutover
remained disabled. This is a local parallel integration, not a production
deployment or cutover, and no Core DDL was performed.

One user's browser QA produced 14 facts. One bucket and one day projection
were ready, both backfills were complete and their run identities matched. The
Query endpoint returned HTTP 200 with 109803 ms total: 45612 ms theory and
64191 ms practice. The UI displayed 1 minute and 9 of 280. These are observations
for that single local flow; overall data quality remains unknown, and a complete
privacy test was not performed.
