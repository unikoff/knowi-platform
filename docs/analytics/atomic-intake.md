# Synthetic atomic intake (CORP-78)

`PostgresIntake(Database)` implements the frozen Collector `IntakePort`:
`accept(record, deadline=...)` takes an absolute `time.monotonic()` deadline.
The caller owns authorization, trusted namespace and catalog admission. This
adapter owns one database transaction, its lock, deduplication and the required
synthetic work records. The adapter does not open the privacy readiness gate.
The Collector factory supplies this adapter by default while retaining explicit
test overrides; default readiness, fence and catalog remain closed.

The transaction acquires the exclusive subject advisory lock, reads the local
fence, and denies a tombstoned subject before duplicate/conflict inspection. It
then acquires the event-dedup advisory lock and looks up the canonical v1 hash;
a differing hash is conflict and never overwrites anything. For a new or matching
fact it inserts one `processing_jobs` row per subscription, with uniqueness on
`(producer_namespace, event_id, consumer, consumer_version)`. Commit precedes
`accepted` or `duplicate`. Missing jobs for a matching old fact are restored in
that transaction. Jobs refer to the fact via an Analytics-local cascading FK;
the subject UUID is the whole lifecycle partition identity. There are no
per-course tables or broker. Collector can insert/select jobs, Processor can
select/update jobs, Privacy can delete them, and Query has no job access.

Only an unknown identity is checked against `metadata_v1` and the shared
admission budget before fact INSERT: future skew beyond 120 seconds and lateness
beyond 7 days reject without inserting a fact. Matching duplicates and conflicting payloads do
not undergo a new wall-clock check, so a committed retry remains reconcilable
after the lateness window.

The public `contracts.work_v1.SYNTHETIC_SUBSCRIPTIONS` currently contains the
independent `synthetic.activity` and `synthetic.independent` consumer identities,
each version 1. They represent synthetic event-count effects only; no consumer,
lease, claim, result or production aggregation is implemented here. Consumer IDs
are stable across replicas. Changes to this registry require a versioned
reconciliation/backfill strategy; retention or compaction of completed jobs must
not erase the consumer's durable deduplication boundary. Retry can recreate a
missing job and future processors must make effects idempotent.

The published lock contract is `contracts.locking_v1.subject_lock_key(UUID)`:
SHA-256 over `b'analytics:subject-lock:v1\0' + UUID.bytes`, interpreted as the
signed big-endian integer in its first eight bytes. The transaction calls
`pg_advisory_xact_lock(key)` before reading even a missing fence row. Future
Privacy and Processor writers must take exactly this exclusive lock first;
future Query must take the compatible shared transaction lock and hold it while
materializing the bounded result. Hash collisions serialize otherwise unrelated
subjects. SQL that updates a fence or deletes facts without this lock is not the
privacy protocol and is not protected by this implementation.

`event_dedup_lock_key(namespace, event_id)` takes SHA-256 of domain
`b'analytics:event-dedup-lock:v1\0'`, a four-byte big-endian UTF-8 namespace
length, namespace bytes and UUID bytes. Its first eight digest bytes are two
signed big-endian 32-bit integers, acquired through the two-integer PostgreSQL
transaction advisory lock. That lock keyspace is distinct from the subject
bigint advisory lock. All intake writers must take subject first and event second.

Timeout and cancellation during commit or after commit have **unknown outcome**.
No ACK is returned; reconcile or retry using the same producer namespace, event
ID and payload. The database rollback handles a failure before commit. This is
at-least-once input with an idempotent transaction boundary, not exactly-once
transport. `IntakeUnavailable` covers timeouts/database errors; cancellation is
propagated. Global quotas and backpressure belong to CORP-80, and processor
claim/fencing belongs to CORP-81. There is no public fault-injection endpoint.

## Schema evolution and rollback boundary

`a002` expands `a001` with technical jobs and backfills its two frozen synthetic
subscriptions for existing non-tombstoned facts. It preserves all fact rows and
is forward-only. The initial synthetic backfill runs in the migration transaction;
it is not a measured or bounded production backfill. Quiesce the old raw writer
before migration and deploy the new intake only after migration. An old writer
that inserts facts after the backfill does not create jobs: a later matching retry
through the new intake repairs them, but until then work is absent. Therefore
rolling application code back to an active old raw writer is **unsafe** without a
separate reconciliation mechanism. The old scaffold can still start with the
expanded schema but has no live fact consumer. This does not authorize a
production rollout, old-writer coexistence or a database downgrade.

## Actual verification

On 16.11.2026 the guarded test runner created a new `analytics_test_<uuid>` DB on
the isolated synthetic PostgreSQL 16 endpoint and removed only that database and
its roles after sentinel/owner checks. `python -B tools/test_intake_postgres.py`
covered: an `a001` fact surviving upgrade and receiving jobs; two independent
Collector instances racing same identity with equal payload, changed payload and
changed subject; failures after fact/before commit; cancellation and deadline;
lost reply after a real commit followed by a duplicate retry; fence-first
serialization with synthetic Privacy deletion; existing tombstone denial even
for duplicates/conflicts; fact/job cascade; role grants; and repair of a legacy
write by retry. Signed JWT HTTP tests also exercised the real PostgreSQL adapter,
clock rejection and an unknown response after a partial batch commit followed by
retry of the same IDs. Readiness, fence and catalog in those HTTP tests were
explicit synthetic stubs; the default privacy gate was separately observed to
remain closed. These tests do not prove full worker fencing, restore, real
deletion acknowledgments, quotas or production performance.

The optional image check built a separate `knowi-analytics:corp78-synthetic` image
and upgraded a fresh disposable DB with a pre-existing `a001` fact; the fact and
its jobs were present afterward. The previous `knowi-analytics:an1-local` Query
scaffold started with that DB URL, returned live 200 / ready 503 and shut down.
Because it does not read facts, this is a startup smoke check, not evidence of an
old data consumer. Set `ANALYTICS_INTAKE_TEST_IMAGE=knowi-analytics:corp78-synthetic`
to include this check; it requires both local image tags and the synthetic network.
