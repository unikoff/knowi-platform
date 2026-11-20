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
the JSON configurations must use matching paths. The prepared values turn off
generic and bucket workers and leave `REAL_COLLECTION` false. This profile alone
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
Check `SHOW ssl` on the actual Core database before provisioning that source.
With TLS disabled, the source cannot connect under the required policy. Rehearse
the server certificate and CA with the intended database hostname and verify
that an incorrect hostname or CA is rejected.
Its HMAC key, database URL, database CA and server TLS keys use four separate
Secrets. Set `coreDeletion` to the Service's namespace, Pod labels and HTTPS
port; its JSON URL hostname must match the server certificate SAN. TCP probes
only establish listening sockets: test authenticated deletion snapshots and
denied client certificates, paths and methods before enabling the cutover.

`knowi01` has a single node and no confirmed complete current deletion journal
outside its restore domain. The owner approved a single-node launch conditional
on keeping old restored history closed until deletion reconciliation. This
preserves the opening requirement of
[CORP-524](https://platformpy.atlassian.net/browse/CORP-524). Verify the
conditional release gate before activation. Provision the separate anchor
database and restricted runtime role using
`services/analytics/ops/privacy_authority/001_anchor.sql`. If the anchor is on
the same node, it can roll back with the journal.

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

For single-node rehearsal, set `runtime.hostBootRecoveryGate: true`. The chart
bind-mounts the host directory `/run/knowi-analytics-recovery` read-only into
Analytics runtimes and the authority. Confirm `/run` is tmpfs. Create the root
owned directory without an `open` file. The `open` file, when issued manually,
must be a root-owned regular file with read-only access for workload users and
contain exactly the current kernel boot ID followed by a newline. Never issue
this permit from a boot unit, a Kubernetes object or a restore script. Cold
boots have no permit. Without one, Analytics denies operational reads and
processing, and the authority denies new append requests. The authenticated
journal read and purge path remains available for controlled reconciliation.

For a logical restore on a running host, first remove the permit, stop and
drain all runtimes and publishers, quarantine restored Core endpoints and revoke
application access to the restore target. Restore into a fresh isolated database
and keep the public route on the old live target or on maintenance. A supervised
promotion after successful reconciliation is the only supported path to issue
the permit for that target. Rehearse this on a separate cluster. The boot permit
alone covers neither a hot VM memory rollback nor an in-place privileged restore
that bypasses the procedure. An unverified restore stays quarantined.

The current `knowi_backup` receiver and offsite pull cover Core CMS archives.
Their verified copies preserve the state at each snapshot. They do not produce
a complete record of deletions confirmed after the latest copied state. The
regular server and offsite rotation can also remove a release rollback point,
so retain that set separately for the release window.

### Option using the existing offsite client

The existing Windows client can be evaluated as an independent destination for
an encrypted, contiguous deletion journal. Its archive acknowledgement covers a
single checked archive. A deletion acknowledgement would need to prove durable
storage of the journal prefix, signing the authority identity, generation,
sequence and prefix digest only after verification. Confirm completion only
after purge and this independent acknowledgement. If the client is unavailable,
completion stays pending. This option requires approval of that behavior and
implementation and testing of the new acknowledgement protocol.

During disaster recovery, obtain a fresh authenticated checkpoint and the full
matching prefix from offsite storage. Keep restored consumers and the restored
journal writer closed until replay and receipt verification succeed and the old
writer is fenced. Test interruption after the offsite write, retry, prefix gaps,
an unavailable client and a conflicting restored writer before using real data.

## Remaining production report gate

The prepared Helm profile admits backend transfer over private mTLS. A later
explicit `browserCollection.enabled` requires a private Core Collector gateway,
publisher acknowledgement, and the activity, day and course projections. The
release build keeps `VITE_ANALYTICS_TRACKER_ENABLED` false. For production
catalog publication use `python -m pymentor_core.app.analytics_catalog
--production --course-id <course>` inside the approved publisher context. Core
uses the separate production receipt directory with read-only credentials. Run
the publisher only after verifying the active Analytics dataset and guard.
Legacy activity dates cannot provide measured study time. Before production
acceptance, demonstrate a protected collection and projection path using an
authorized non-staff learner and a published catalog version for the intended
course. Use a leader authorized for that course to read the visit series in
`GET /api/admin/courses/{course}/overview` and the measured time series in
`GET /api/admin/courses/{course}/study-time`. Check the expected positive values
against the control activity and verify the corresponding calls to Analytics
Query. These endpoints return cohort aggregates, so an HTTP response alone
cannot attribute the values to the control learner. Confirm attribution using a
course and cohort with a known baseline and the expected control contribution.
