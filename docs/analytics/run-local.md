# CORP-62: local synthetic runtime

## Локальный a010 release

Проверенная source-цепочка заканчивается на a010; её продвижение само не меняло базы. Revision-specific роли, ограничения старых backup и порядок миграции описаны в [local-a010-release.md](local-a010-release.md). Чтение документа не запускает migration и не разрешает изменять сохранённые базы.

Scope: disposable synthetic data only. The isolated PostgreSQL project
`knowi-analytics-01a1df75`, its volume and `127.0.0.1:57392` are separate from
Core PostgreSQL on 57382 and Core Redis on 57381. These commands never delete a
database, role, volume or cluster. Compose may recreate its own application
containers when their configuration changes. `stop` stops only the explicitly
named application project, not PostgreSQL. This is not a production runbook.

## Compose runtime

From `services/analytics`, prepare a Python 3.12 environment using the Analytics
requirements. On a fresh checkout, prepare the independent credentials, build
the current synthetic image and start the designated PostgreSQL project:

```powershell
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B tools/setup_local_postgres.py
.\.venv\Scripts\python.exe -B tools/setup_synthetic_trust.py
docker build -t knowi-analytics:local-synthetic .
$env:ANALYTICS_IMAGE = 'knowi-analytics:local-synthetic'
docker compose -p knowi-analytics-01a1df75 -f ../../infra/analytics-local/compose.postgres.yaml up -d
```

The second helper is optional when authentication is deliberately unconfigured.
Each helper creates new files exclusively; it validates and preserves matching
existing files. Unknown, symlinked, hard-linked, partial or conflicting state is
refused. A valid PostgreSQL metadata file can restore its missing matching env
file, but an env file without metadata is refused. The trust helper requires
both private keys and the exact matching public manifest to be present together;
it does not regenerate or replace a partial set. A leftover `.*.pending` file
means creation was interrupted: preserve it and reconcile manually. Atomic
creation uses hard links on the same filesystem and fails closed if unsupported.
Restrict `.local` with OS access controls; Windows mode bits alone do not enforce
private ACLs. Never publish, echo or place the private signing keys in an image.
The helpers do not start Docker, initialise a database volume or rotate credentials.
If an existing volume has different credentials, creating a new env file does not
change the database password; reconcile rather than remove or recreate the volume.

Once the designated PostgreSQL is healthy:

```powershell
.\.venv\Scripts\python.exe -B tools/run_local.py init
.\.venv\Scripts\python.exe -B tools/run_local.py upgrade
.\.venv\Scripts\python.exe -B tools/run_local.py start
.\.venv\Scripts\python.exe -B tools/run_local.py status
.\.venv\Scripts\python.exe -B tools/run_local.py stop
```

`init` validates the ignored `.local/postgres-connection.json`, creates a
distinct `analytics_test_<uuid>` DB with separate roles and a random sentinel,
persists `.local/runtime.json` **before** invoking the explicit migrator and
verifies the current single Alembic head. Repeated init verifies metadata,
sentinel, owner and a known revision and reuses the same DB; it never upgrades
or reseeds an existing DB. Use `upgrade` as a separate explicit action for an
existing older schema; `start` never performs DDL and refuses an old schema.
Neither initialization nor revision head means privacy readiness. Metadata includes role
passwords and a nonce: retain it in the ignored `.local` directory with restricted
OS ACLs, do not print, commit, share or mount it in application containers. The
database password exists in each container environment and local Docker
administrators can inspect it; use only disposable synthetic credentials.

The retained runtime was at `a001` before this tooling change; `a003` is still
under review. This change did not upgrade the retained database or start the
runtime. Run `upgrade` only after migration and image review and an explicit
operator action.

If provisioning is interrupted, preserve both metadata and the DB. A
`provisioning` state may mean roles or a database exist without a recorded name;
the existing bootstrap cannot identify those from the marker. An interrupted
`migration_pending` state may be reconciled on repeat only if sentinel, owner,
recorded target, fingerprinted source history and actual single head all match.
The tool never repeats an uncertain migration automatically. Otherwise stop and manually inspect the designated synthetic
cluster; do not remove the marker or retry by creating another DB. No automatic
cleanup or recovery is claimed. A stray `.local/runtime.json.pending` or
`.local/runtime.operation.lock` also needs manual reconciliation after verifying
that no prior process or database migration is still running. Never apply the
test helper's cleanup to this persistent runtime. `upgrade` persists target and
SHA-256 of the complete linear history before invoking the existing migrator;
multiple heads, unknown DB revisions and changed source histories fail closed.
Do not edit migration sources concurrently with an upgrade: the migrator uses the
recorded target revision, and a history change detected after invocation cannot undo DDL.
Rolling back app code does not roll back durable schema or data.

`start` checks the local Docker socket, existing project labels, DB sentinel and
schema, inspects actual Compose memory limits, and measures free host RAM
immediately before starting containers. The current three 256 MiB application
limits and 128 MiB ingress, plus 358 MiB PostgreSQL headroom and 512 MiB host
reserve, round up to a 1792 MiB admission threshold. If all three application
limits become 128 MiB, the corresponding threshold is 1536 MiB. These are
conservative synthetic smoke budgets, not capacity or performance proof. Missing
or excessive memory limits refuse startup. It uses the separate application project
`knowi-analytics-app-01a1df75`, existing external Analytics network and the
locally available `knowi-analytics:an1-current` image (override with
`ANALYTICS_IMAGE` only for a reviewed local image). Role URLs are passed through
the process environment without being printed, with Docker address
`analytics-postgres:5432`. The image must already exist; no pull is requested.
Before starting the app, the tool resolves the selected local image to its
immutable Docker image ID and runs a bounded, network-disabled, read-only probe
without credentials. It compares the image's exact linear Alembic revision/file
hash and a hash of all runtime Python files, `requirements-runtime.txt` and
`alembic.ini` with the current source. Compose receives the verified ID, not the
mutable tag. A source change before startup refuses it; a change during startup
stops the application project. This compares copied source and dependency pins,
not the packages actually installed in the image or the safety of arbitrary
untrusted images. Build and review the intended image; an old image fails the
comparison. A timed-out image probe is removed only after its unique name, image
ID and nonce are verified; if cleanup is unknown, inspect that dedicated probe
before another attempt.
`status` reports process state and observed probes, not readiness certification.
`status` and `stop` need neither live PostgreSQL nor runtime credentials: they
use placeholder interpolation after checking the local Docker endpoint and
project identity, so a database outage does not prevent stopping the app.
`/ready` must stay 503 until the real privacy authority, safe schema and other
role-specific dependencies are implemented. A process
or route returning 404 does not implement event intake or reports.

Optional `.local/trust-manifest.json` contains **public** grant and producer
verification keys, issuers and producer allowlist. `start` validates it with the
same `load_manifest` as the app, rejects role-specific conflicting trust
environment variables, and writes an ignored `trust-override.json` containing
only the common public manifest. It never places database URLs there. Collector
and query must return the same expected `X-Analytics-Trust-Fingerprint` on their
internal `/ready`, including 503, and the source must remain unchanged through
startup; failed verification stops the app project. Missing manifest leaves
authentication unconfigured. The image must include this header implementation;
the older `corp63-synthetic` image does not. Fingerprint equality only confirms
the public trust configuration, not privacy, authorization or data readiness.
Private signing keys must never be placed in the app, manifest or output.

## Explicit synthetic replay

Before the **first** replay `begin`, retire every pre-replay Processor and Query,
including copies outside this Compose project. Stop this project's application
stack with `tools/run_local.py stop` and verify its Processor and Query are
stopped with `status`. The CLI then checks the labels and state of all owned
containers and requires zero PostgreSQL sessions for the exact retained runtime
processor/query roles, with sufficient session visibility. It will refuse to
begin rather than stopping anything itself. This point-in-time check cannot
prevent an unmanaged old process or remote deployment from reconnecting; retire
those separately before proceeding. After `begin`, start only the rebuilt,
dirty-aware image that passes the source/image guard. `step` and `finish` may run
with that verified stack active. Do not roll back to an old processor or query
while replay is active **or after new-protocol data has been created**; application
rollback does not undo schema, receipts, replay state or dirty generations.

`tools/replay_synthetic.py` performs **one** `begin`, `step` or `finish` per
invocation, using only the retained synthetic runtime's processor role. It checks
the local maintenance lock, runtime metadata, sentinel, DB owner and current
single migration head first. It does not run migrations or a background loop.
The supported v1 consumers are `synthetic.activity` and `synthetic.independent`.
Supply a synthetic subject UUID and keep one request UUID for the entire replay:

```powershell
.\.venv\Scripts\python.exe -B tools/replay_synthetic.py begin --consumer synthetic.activity --subject-ref <synthetic-subject-uuid> --request-id <request-uuid>
.\.venv\Scripts\python.exe -B tools/replay_synthetic.py step --consumer synthetic.activity --subject-ref <synthetic-subject-uuid> --request-id <same-request-uuid> --generation <generation> --subject-epoch <epoch> --limit 100
.\.venv\Scripts\python.exe -B tools/replay_synthetic.py finish --consumer synthetic.activity --subject-ref <synthetic-subject-uuid> --request-id <same-request-uuid> --generation <generation> --subject-epoch <epoch>
```

Replace the placeholders with values, without angle brackets. Generation is at
least 1, epoch is at least 0, and the step limit is 1–100. The adapter also caps
new replay jobs per step at 16; `blocked` requires operator attention and no
automatic retry. Results expose phase, request ID, generation, epoch and blocked
only; they do not print the subject or DB credentials. A completed command is not
a privacy-readiness or real-data authorization check.

An error, interruption or timeout can occur after a commit, so its outcome is
unknown. Before another operation, inspect the persisted replay state in the
designated synthetic DB using the same consumer, version, subject and request
identity, and account for any still-running transaction. There is no dedicated
read-only status command in this CLI. In a read-only transaction on that DB,
the processor role can run the following parameterized query, binding values
privately (do not put credentials in the command line):

```sql
SELECT phase, generation, subject_epoch, cursor_namespace, cursor_event_id
FROM analytics.processing_replays
WHERE consumer = :consumer AND consumer_version = 1
  AND subject_ref = :subject_ref AND request_id = :request_id;
```

A repeated `begin` with the same ID returns
an existing replay, but **creates one if absent**; it is not a read-only
reconciliation operation. Only after state is reconciled may an operator choose
the next bounded operation using the same identity. Never use a fresh request ID
as a blind retry, and do not assume a timed-out `step` failed to advance.

Observed 16.11.2026: guarded initialization and repeat reconciliation succeeded;
with the 1792 MiB application admission check satisfied, all three processes
started, each returned `/live` 200 and `/ready` 503, and the application project
was then stopped. A subsequent status showed all four application containers
exited. PostgreSQL and its volume were retained. These checks used the synthetic
`corp63-synthetic` image before later auth integration, and do not establish
runtime readiness or functional event/report routes.

## K3s admission and prepared configuration

`tools/check_k3s.py` is read-only and requires at least 2560 MiB available host
RAM plus a verified local Docker endpoint. An admission pass is not a cluster
health result. Current execution is **blocked** on memory; no cluster has been
created. The latest observation was 1681 MiB available. k3d 5.9.0 accepted the
config through `config migrate`; that is a config parsing check, not a cluster
test. Do not stop unrelated applications or change WSL configuration to pass.

After a fresh successful admission and a separate decision to proceed, inspect
Docker containers and k3d clusters without outputting kubeconfigs or tokens.
Refuse an existing same-named cluster until its server identity and Docker labels
are reconciled. The only target is `knowi-analytics-01a1df75`; its server must be
the k3d server for this cluster with `knowi.analytics.synthetic=true`. Do not
reuse a pre-existing Kubernetes context. The prepared config
`infra/analytics-local/k3d.yaml` pins K3s `v1.35.5-k3s1`, one server, zero agents,
loopback API 57443, disabled Traefik and ServiceLB, and disables default
kubeconfig updates and automatic rollback. When authorised and admitted, the
k3d 5.9.0 create command must also pass `--servers-memory 2g` and both explicit
`--kubeconfig-update-default=false --kubeconfig-switch-context=false`; the cap is
a CLI argument, not encoded in the YAML. Use a bounded timeout. Capture the
generated kubeconfig only into an exclusive private file under `.local` and use
that path and the exact `k3d-knowi-analytics-01a1df75` context on every kubectl
command. Never print its contents or merge it into a global config.

The create invocation (only after those admission and identity checks) is:

```powershell
services/analytics/.local/bin/k3d.exe cluster create --config infra/analytics-local/k3d.yaml --servers-memory 2g --kubeconfig-update-default=false --kubeconfig-switch-context=false
```

The future cluster exercise must explicitly import the reviewed local Analytics
image into that cluster, create only namespace `knowi-analytics`, provision
separate synthetic role Secrets without exposing them on command lines, establish
and verify the isolated DB network/address and its exact egress CIDR, then install
the existing chart with ingress disabled. Validate server/container identity again
before each mutation. Run probes, network-denial and rollback checks only against
that cluster. Traefik is disabled, so no IngressRoute can pass until a separately
installed controller and CRDs are verified. The helper does not automate these
operations or deletion; Kubernetes workload, CNI, ingress, migration Job, restore,
and readiness have not been tested in K3s. Do not infer CORP-109 acceptance.
