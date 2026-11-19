# Core snapshot proof v1: local implementation

This is a default-off implementation and proposed a009 migration. It is not
deployed, and local source tests do not establish PostgreSQL or stage acceptance.
It is independent of the Core transfer v1 raw event schema.

## Source and wire

The P1 producer is selected by the configured server-side namespace mapping.
`POST /internal/core-transfer/v1/proofs` accepts `{proof,fingerprint}` and
returns `{status:accepted|conflict}`. Accepted means the proof was committed for
later verification, not that coverage was verified. `GET
/internal/core-transfer/v1/proofs/{proof_id}?fingerprint=...` returns
`{status,coverage}`: absent/conflict have null coverage; accepted has pending,
verified or unavailable. Non-200, timeout and lost response are unknown; reconcile
with the same identity before retry. P1 authorizes writing, never Query reads.

Proof fields are `schema_version=1`, `proof_id`, lifecycle `subject_ref`,
`scope="subject_all_v1"`, nonnegative `cut_revision`, `entity_count`,
`entities_digest` and UTC `created_at` in canonical `+00:00` form. Canonical
JSON sorts keys, uses compact separators and UTF-8 without ASCII escaping.
The proof namespace is UUIDv5 of URL namespace and
`https://pymentor.ru/contracts/core-analytics-snapshot-proof/v1`. The proof ID
is UUIDv5 of that namespace and the canonical proof excluding `proof_id`;
fingerprint is SHA-256 of the complete canonical proof.

The entity digest starts with `pymentor:core-snapshot:v1` followed by a zero
byte. Sort entities by the existing Core-transfer `entity_key` bytewise; for
each active entity append canonical `{"kind":...,"payload":...}` and LF.
Deleted progress is excluded from digest/count, but its latest tombstone still
requires a durable processor receipt. The source bounds are 10,000 activity
days, 5,000 active progress rows and 8 MiB including the digest prefix.
Verification also limits the historical distinct entity keys to 20,000. A bound
failure never yields partial completeness.

The Core owner captures source snapshot, revision and proof under its existing
lifecycle/write gate and commits its outbox atomically, with no network inside
that transaction. Legacy backfill barriers must be sealed and all Core writers
must be capture-enabled before Core relies on the source revision. Core sender
reconciles proof acceptance separately from verified coverage.

## Analytics verification and reads

Under the subject advisory lock, the worker pages latest immutable Core facts
with revision at most the cut, checks canonical identity/hash and an atomic
projection receipt for every latest record, computes the count and digest and
then atomically marks the proof verified at the current subject epoch. A pending
out-of-order fact/receipt keeps it pending; bounded retry uses backoff and
quarantine, and a relevant newly accepted fact or completed projection wakes it.
Only one verified proof per namespace/subject is current; a newer verified proof
supersedes the old receipt without deleting it. New facts with revision greater
than the cut do not change the historical proof. An a009 database trigger
invalidates verified coverage in the same transaction as every newly inserted
Core fact at or below its cut. The trigger only downgrades status; the collector
cannot insert or update a proof directly into verified state.

Query checks verified proof and epoch under the same subject locks as its read.
It rejects the read if the current projection contains a revision above the
verified cut; it never labels a newer partial projection complete. It does not
scan raw history per report. A verified self response adds
`coverage_through_revision`, `source_snapshot_at`, `source_hwm:null` and
`freshness:"unknown"`. Core must recheck its lifecycle and current revision
after the network response and reject changed/mismatched revisions. The result
is a bounded as-of observation, not current-now freshness.

For admin complete coverage, the G1 admin/global/report grant may contain the
optional `legacy_admin_roster_v1` claim: `{version:1,members:[{subject_ref,
revision,student},...]}`. The signed list has at most 80 unique subjects, and
the full JWT retains the 16 KiB bound and existing short TTL. Query checks each
exact cut and epoch, verifies no newer projected revision, and returns the
`coverage_subjects` vector. Core must compare its complete authorized roster,
roles/lifecycles and revisions after the response. Missing claim/proof,
incomplete scope, stale roster, overflow or newer projected revision is 503.
The older environment roster is only a shadow/unknown-coverage fixture.

Older G1 issuers omit the new optional claim and remain compatible with the new
verifier. Older strict Analytics verifiers reject a new claim, so deploy the
updated Query verifier before Core starts issuing it. An ordinary public grant
must not gain this internal roster claim.

## Privacy and rollout

The proposal stores subject proofs in `core_snapshot_proofs`, separately from
facts. The privacy worker deletes them in bounded pages and checks their absence
before completing a deletion receipt. The checked deletion endpoint also tests
their absence when a009 exists. Restore must replay the independently verified
privacy journal before readiness; copied proof rows never bypass that barrier.
The trigger also covers a007 writers after a009, but older a007 processor and
Query binaries lack the proof gate and must be retired before verification/read
cutover. No TTL or destructive data contraction is
introduced; schema rollback after new proof data requires an explicit data plan.

After a007/a008 prerequisites and all external gates, a009 is still a proposed
forward-only migration. Enable separately:

- `ANALYTICS_COLLECTOR_CORE_TRANSFER_PROOF_ENABLED=true`
- `ANALYTICS_PROCESSOR_CORE_TRANSFER_PROOF_ENABLED=true`
- `ANALYTICS_QUERY_CORE_TRANSFER_PROOF_ENABLED=true`

Each requires that role's existing Core-transfer feature flag; absent a009 is
fail-closed. With proof flags off, the first a007 harness keeps its existing
shadow behavior. PostgreSQL migration/role permissions, concurrent late fact,
source cutover, deletion, restore, limits and actual end-to-end response remain
to be tested in an explicitly authorized disposable environment.
