# Analytics production preparation — not an activation

`application.yaml` is illustrative and does not activate this overlay. The
prepared values keep migration, authority, ingress, privacy worker and feature
flags off. Rendering is not evidence that an Argo application, Secrets, storage,
database or network policy enforcement exists on the target cluster.

The explicit `runtime.mode: production` profile wires the shared trust manifest,
privacy authority and Core deletion source to all roles and the migration Job;
collector, processor and query additionally receive the deletion status DB
Secret, and privacy alone receives the relay Secret. The two mTLS Secrets are
mounted at `/var/run/analytics-authority-tls` and `/var/run/core-deletion-tls`;
the JSON configurations must use matching paths. Processor generic and bucket
workers are disabled and `REAL_COLLECTION` remains false. This profile alone
does not enable collection or constitute an accepted Core–Analytics cutover.
Deletion source egress permits only the selected Core namespace and Pod labels;
the configured port must be the actual destination Pod port.

Before activation, provision role-specific Analytics database credentials and
the separate migration credential outside Git. Use an immutable image digest,
an independently backed-up Analytics PostgreSQL database and an explicitly
reviewed a014 migration operation. No credentials or private keys belong in
values or ConfigMaps. Verify current schema, role grants, backup and tested
restore before running the migration; a Deployment rollback does not undo it.

The optional authority Deployment uses a single replica and `Recreate`, an
internal ClusterIP, mTLS, an existing journal claim and an independent PostgreSQL
anchor. The private Secret is mounted at `/var/run/authority`; `config.json`
must reference the journal under `/var/lib/authority/journal` and configure
`anchor_backend=postgres` with a DSN file, CA file, expected host, database and
user as required by the authority server. Network egress permits its exact
anchor address, but neither an address nor a Secret name proves independence.
Its bind host and port must match the Pod port. Provision the anchor schema and
initial row as documented by the authority implementation; initialize the
journal once through an explicit controlled operation. Serving does not create
state. Never recreate the journal during rollout or recovery. The journal and
anchor need separately managed failure and backup domains. One node's
`local-path` storage does not establish independence, and a `Delete` reclaim
policy is not a retained recovery plan.

Restoring both journal and anchor to the same old state cannot be detected by
the authority. Protect the PostgreSQL anchor independently against rollback and
test the recovery procedure before production cutover. TCP
readiness proves only that the server accepts connections, not journal integrity
or authorization; require an authenticated application-level check. Restore or
rollback must not reopen data access until the deletion state and checkpoint
have been reconciled. Do not run synthetic authority against real data.

The authority ingress policy permits only selected Analytics Pods and a named
Core namespace plus explicit Pod labels. Confirm actual namespace/Pod labels,
Core egress and CNI enforcement with both allowed and denied connection tests.
Secrets must contain correctly issued server/client certificates and an HTTPS
hostname matching the server certificate SAN. Core's egress policy is managed
separately. The chart does not provision certificates, PVCs, backups, a trusted
monotonic checkpoint or database roles, and it does not make a cutover safe.

Production Collector and Query listen on loopback and use separate nginx mTLS
sidecars, certificates and internal Services. Their allowlists do not expose
the browser ingestion endpoint or arbitrary HTTP paths. The proxy image must be
a verified immutable frontend image containing nginx. The optional private Core
deletion source is a separate process with a read-only Core DB role, pinned Core
image, loopback listener and mTLS sidecar; it is disabled by default. Its source
DB URL must use `sslmode=verify-full` and `sslrootcert=/var/run/source-db-ca/ca.crt`.
Its HMAC key, database URL, database CA and server TLS keys use four separate
Secrets. Set `coreDeletion` to the Service's namespace, Pod labels and HTTPS
port; its JSON URL hostname must match the server certificate SAN. TCP probes
only establish listening sockets: test authenticated deletion snapshots and
denied client certificates, paths and methods before enabling the cutover.

The currently inspected `knowi01` has a single node and no confirmed complete
current deletion journal outside its restore domain. Keep production activation
closed under [CORP-524](https://platformpy.atlassian.net/browse/CORP-524). An
additional database or PVC on the same disk still rolls back with the node.
Once an approved existing independent store is available, provision and verify
the anchor using
`services/analytics/ops/privacy_authority/001_anchor.sql`; runtime authority
must receive only the narrowly granted role described in that SQL. An operator
able to restore or alter the whole anchor database can still roll it back.

The candidate Core migrations Dockerfile packages
`services/core-api/tools/analytics_transfer/production_migrate.py` and
`production_backfill.py`. Drain all Core writers and connections, verify the
target identity, revision, backup and role boundaries, then use the guarded
migration plan/apply path. Resume capture in `mirror` mode and use the bounded
backfill operator to provision lifecycles and initialize, page and seal both
activity and progress streams. Check status and validate the resulting proofs
before changing the read path. Reconcile an unknown operation outcome from
status before retrying; do not assume a timed-out operation failed.

## Routine release rollback

Keep Core writes in `mirror` for the first cutover. Roll back the legacy read
path with `read=off` and disable the Analytics-only report flags as needed.
Keep mirror capture, both deletion paths, the current authority and the privacy
worker. Core has a fallback for legacy calendar/day reports. Course-level
measured-time reports need Analytics and can become unavailable on rollback.

An approved release-window data restore requires a separately retained backup
set with exact build digests, database revisions, deletion sequence, archive
hashes and a checked restore result. Stop every writer, including account
deletions, before taking the snapshot. Hold that write barrier through the
rollback and verify that no writes, deletions or deliveries committed after the
snapshot. Once new writes are accepted, use the read-path rollback. Restoring
that old data then needs a separate reconciliation plan.

## Disaster recovery from an older snapshot

Quarantine the restored target before allowing Core or Analytics to read,
process or deliver its data. Compare with a trusted current deletion journal
and independently verified checkpoint outside the restored state. Apply the
missing prefix, verify purge receipts and the control account, then open the
live stream from the reconciled sequence. Leave the restored target closed if
the journal is absent, stale, incomplete or damaged. Replaying two copies from
the same snapshot cannot establish their freshness.

The current `knowi_backup` receiver and offsite pull cover Core CMS archives.
Their verified copies preserve the state at each snapshot. They do not produce
a complete record of deletions confirmed after the latest copied state. The
regular server and offsite rotation can also remove a release rollback point,
so retain that set separately for the release window.

## Remaining production report gate

The prepared Helm profile admits backend transfer over private mTLS. The
browser activity input and its bucket workers are disabled in this profile.
Legacy activity dates cannot provide measured study time. Before production
acceptance, demonstrate a protected collection and projection path using an
authorized control lifecycle, then verify the Core course reports return that
account's expected nonempty Analytics values. A ready pod or a positive query
against a synthetic projection does not establish this gate.
