# Core transfer v1 receiver: local integration

This is a local implementation with default-off factory wiring, not an accepted rollout.
The canonical producer is `services/core-api/contracts/analytics-transfer-v1.md`.
Analytics has no Core database access or Core runtime imports. The test imports
the independent Core contract to compare its actual normalization and hash.

## Wire and admission

The opt-in router exposes `POST /internal/core-transfer/v1/events` with
`{"event": ..., "fingerprint": "<lowercase SHA-256>"}` and `GET
/internal/core-transfer/v1/receipts/{event_id}?fingerprint=...`. A 200 response
is exactly `{"status":"accepted"|"absent"|"conflict"}`. Accepted means the
fact and its processing job committed together; it does not mean the projection
or reports are up to date. Invalid payload is 400; authentication is 401;
deleted subject is 403; overload is 429; unavailable and unknown outcomes
are 503. Reconcile by receipt after a timeout before retrying the same event.
After a completed purge the fact no longer identifies its subject, so receipt
lookup can return absent; the deletion protocol, not a receipt lookup, is the
source of truth for privacy retirement.

`app.openapi()` describes these routes when enabled. The default app does not
mount them. Deployed OpenAPI comparison and generated-client compatibility
remain unavailable until integration and authorized runtime validation.

The installed P1 `ProducerVerifier` validates issuer, audience
`analytics-control`, signature and allowlisted subject; router enforces
`control:write` and a server-owned producer-to-namespace mapping. Never derive
namespace from body or query. Configure separate namespaces so browser and Core
identities cannot overlap. A readiness dependency must fail closed until the
verified privacy barrier, database schema, roles and integration gates pass.
The router limits body bytes to 8192, concurrency and overall deadline.
Core uses sorted, compact, UTF-8 JSON with `ensure_ascii=False`; the full event
is hashed with SHA-256 and its `event_id` is UUIDv5 of the canonical body
without `event_id`. Both are validated. Subject deletion is rejected here.

`GET /internal/core-transfer/v1/deletions/{control_id}?subject_ref=<uuid>&epoch=<positive integer>`
uses the same auth. Without an injected `DeletionStatusPort` it is closed (503).
`CheckedDeletionStatus` reads the local receipt, obtains a fresh signed barrier
and exact page from a **dedicated** `AuthorityClient`, then locks the subject
and verifies journal identity, current applied prefix, fence and absence of all
subject-owned data. It returns accepted only after all checks. A lone fence is
insufficient; `pending`/`absent`/`conflict` do not acknowledge deletion.
The existing AuthorityClient supports explicit fixture-mode transport only; its
use in a production deployment requires a separately implemented and verified
production authority transport. Never share its session with PrivacyRuntime:
`attach` invalidates that client's prior session.

## Storage and projections

The proposed a007 Python and SQL under `services/analytics/plans` are not active
migrations. They add `source_kind`, default existing and old-writer records to
`browser_v2`, and only permits null course/catalog/started_at on
`core_transfer_v1`. Existing browser DTO validation stays strict. Both sources
share `activity_facts`. Core raw events have one entity/revision identity and
one job, inserted under subject and event locks in one transaction. The
subject lock is the existing privacy lock. Different payload at the same
entity/revision is a conflict. Revision 0 never supersedes a positive revision.

`project_one` is a bounded, short database transaction: subject fence, one
locked job, monotone latest-revision pointer, processing receipt and completed
job commit atomically. There is no external work or lease carried across
transactions; rollback leaves pending work. Subject candidates rotate locally,
and their shared advisory lock precedes job row locks. The optional
`CoreTransferRuntime` is disabled by default; when enabled it checks readiness,
limits operation time, yields after each transaction, uses bounded exponential
retry with jitter and cooldown, and cancels on shutdown. Corrupt raw events are
quarantined. After a completed job is compacted its durable processing receipt
still proves acceptance. No end-to-end exactly-once claim is made.
Repeated processing converges without reapplying an older revision. The pointer
joins to the ledger for original payload: legacy activity retains client date,
nullable visited_at and global provenance; lesson progress retains its actual
course/lesson and nullable completion tombstone. Neither grants a fabricated
catalog version, course attribution for global activity, time or timestamp.
Core remains authoritative for progress.

The internal `read_subject` query obtains a fenced, bounded current projection
snapshot (10,000 legacy days, 5,000 weekly completions), returning unavailable
instead of silently truncating. `compatibility` computes calendar, streak and
Moscow completion-day semantics from those rows. `admin_activity` requires an
already authorized, complete set of global visits and Core-provided student
identities. `read_admin` takes an explicitly authorized roster of at most 200
subjects, acquires subject locks in deterministic order and reads the last
30 dates; larger or incomplete rosters require a separate bounded paging and
snapshot design. There is no claim of a complete backfill. Neither a successful
query nor these helpers prove complete coverage;
the future caller must gate on reconciled backfill and projection freshness.

The default-off Query bridge provides `/internal/analytics/v1/legacy/calendar`
(`year`, `month`) and `/internal/analytics/v1/legacy/summary` for verified G1
`report:read` grants (user, leader or admin); the subject comes only from the grant. The separate
`/internal/analytics/v1/legacy/admin/activity` requires an admin global grant
and server-configured complete roster. Every successful response has
`coverage: "unknown"` and `data` when snapshot proof is disabled, so the observed
values are shadow evidence. When separately enabled, the proof workflow can
return complete only under the checks in [core-snapshot-proof.md](core-snapshot-proof.md).
The admin data includes the
30-day dashboard activity, but the roster remains an operator-supplied assertion,
not independent coverage proof. A missing, expired, oversized or deleted-subject
roster fails closed; refreshing it currently requires process reconfiguration.

The Query bridge also exposes `GET /internal/analytics/v1/legacy/admin/profile`
with exactly one canonical `subject_ref` UUID and `year`/`month` query value.
It requires a verified G1 admin/global `report:read` grant, locks and checks
both the administrator and target fences, and always requires a verified
target snapshot proof with no newer projection. Core must separately authorize
the target and compare its lifecycle and revision before and after the request.
The response `data` contains `activity_days_count`, `activity_days`,
`total_activity_days`, `last_activity_date`, and `activity_summary`; coverage
metadata describes the Core prefix only. When a010 is enabled, the optional
`browser_quality`/`browser_as_of` pair describes the browser observation, whose
completeness remains unknown. All counts are bounded by the underlying subject
snapshot; an unavailable proof, fence or projection returns unavailable rather
than a partial profile. The endpoint has signed-HTTP and source-level tests;
the new cross-service profile path still requires live validation.

`core_transfer_entities` and `core_transfer_projection` both carry a subject
and cascade from the subject-coherent ledger identity; the projection also
references the exact entity revision and event. The privacy completion check
explicitly proves both new tables empty under the
same subject epoch/fence; test concurrent accept/project/purge plus restore.
Retain tombstones indefinitely. The new projection is not yet wired to public
calendar, streak or admin reports; parity must use the Core fixtures, original
client dates and Moscow calendar boundaries, including NULL/unavailable values.

## Integration and release gates

1. Keep existing a006 image and schema in service until its acceptance. Build
   a real reviewed a007 migration from the proposed fixture; verify version,
   lock duration, old-writer/new-schema overlap and backup/restore on the
   isolated environment. Schema-only rollback is unsafe once new facts exist;
   application rollback preserves rows, and narrowing nullable columns requires
   a separately proven data conversion or removal plan.
2. New synthetic replay, quality and processor code detects a006/a007 and filters
   by source on a007. Old running binaries still scan all facts as v2; retire
   them before accepting Core facts. Prove mixed-source correctness. Provide
   a controlled, bounded replay/rebuild for this projection; the existing
   synthetic replay must never re-enqueue Core facts as browser events.
3. The proposed Python migration grants needed rights: collector SELECT/INSERT on new entity table;
   processor SELECT entity, SELECT/INSERT/UPDATE projection and job/receipt
   rights; query SELECT on the projection and necessary ledger columns;
   privacy SELECT/DELETE on new tables; a pre-provisioned, dedicated NOINHERIT
   status role receives only the columns required for purge existence checks.
   Verify role attributes, grants, cascades and migration on PostgreSQL. The
   SQL fixture alone does not grant privileges.
4. Verify the default-off factory wiring and monitor pending age/quarantine.
   `project_one` performs one transaction per attempt and holds no lease across
   work. The enabled collector has a dedicated status-role pool and client.
   Keep it closed until journal, all purge obligations, P1 auth and mixed-source
   failure tests pass. Do not wire normal `subject_deleted` into the projector.
5. Verify read-only report parity and safe backfill overlap with live positive
   revisions, latest revision/tombstone ordering, lost reply and reconciliation,
   identity conflicts, crash and rollback, deletion races, recovery and old
   consumers. Formal acceptance remains dependent on the external stage gates.

Tests under `tests/core_transfer/test_postgres.py` are opt-in and generate a
sentinel-guarded disposable synthetic database and test the proposed migration
and distinct runtime roles; they are not evidence until run.

Configuration, with all feature flags false by default:

- Collector: `ANALYTICS_COLLECTOR_CORE_TRANSFER_ENABLED`,
  `ANALYTICS_COLLECTOR_CORE_TRANSFER_NAMESPACES_JSON` (producer-to-namespace),
  `ANALYTICS_COLLECTOR_CORE_TRANSFER_STATUS_DATABASE_URL` (dedicated status role).
- Processor: `ANALYTICS_PROCESSOR_CORE_TRANSFER_ENABLED`.
- Query: `ANALYTICS_QUERY_CORE_TRANSFER_ENABLED`,
  `ANALYTICS_QUERY_CORE_TRANSFER_NAMESPACE` and optional
  `ANALYTICS_QUERY_CORE_TRANSFER_ROSTER_JSON` with `complete`, `expires_at`,
  `subjects` and `students`. Students must be a subset of the complete roster.
- Optional collector catalog uses `ANALYTICS_COLLECTOR_CATALOG_ENABLED` and
  `ANALYTICS_COLLECTOR_CATALOG_NAMESPACE`, sharing the same server P1 mapping.
  It is mutually exclusive with the fixture catalog and requires proposed a008.

Enabled components require the shared `ANALYTICS_TRUST_MANIFEST_JSON` and
`ANALYTICS_PRIVACY_AUTHORITY_JSON`. Missing/incomplete a007 or a008 is not an
opening condition; the feature-specific readiness fails closed.
