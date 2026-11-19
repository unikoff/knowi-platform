# Synthetic same-version replay (CORP-85)

`PostgresReplay(Database)` implements `ReplayStore` for the two existing
synthetic version-1 consumers. `begin(consumer, version, subject, request_id)`
returns a `ReplayState` containing a durable `ReplayHandle`; `step(handle,
limit=100)` advances one bounded page and `finish(handle)` attempts coverage
verification. A repeated request ID returns its existing generation and phase,
never resets it. A different active request for the same partition raises
`ReplayBusy`; a new request after completion starts a higher generation. An old
handle is rejected as `ReplayStale`. Subject tombstones raise `ReplayForbidden`.
Timeout/lost reply has an unknown outcome: call `begin` with the same request ID
to reconcile before another bounded step. These are operator-facing storage
primitives, not a public API or an authorization policy.

All transitions take the versioned subject advisory lock before lease or budget
locks, then verify the subject epoch. Begin invalidates the previous processor
lease generation and marks the projection dirty. The phases are:

1. **cleanup** — processor claims for this consumer/subject are blocked. Each
   step removes at most 100 receipts and the single synthetic result. Existing
   facts and jobs remain. Cleanup uses an owner-owned `SECURITY DEFINER` function
   with a fixed `pg_catalog, analytics` search path, static qualified SQL, its
   own subject lock, bounded arguments, current request/generation/epoch and
   tombstone checks. PUBLIC cannot execute it; Processor gets only EXECUTE and
   cannot directly DELETE receipts/results.
2. **scan** — walk immutable facts by `(producer_namespace, event_id)` in pages
   of at most 100. A missing receipt causes a missing or previously completed job
   to be inserted/reopened; an existing live pending job is left alone. A prior
   quarantine may reopen once in this generation. Worker failures during this
   generation carry the current replay ID and cannot be reopened again by the
   same scan. Checkpoint and changed jobs commit together. Processing is enabled
   during scan, avoiding deadlock when there are more facts than queue slots.
3. **drain** — scanner reached the end. Workers may continue; `finish` keeps the
   result dirty while any fact lacks a matching receipt. The final anti-join and
   transition to complete occur under the subject lock and unchanged epoch.
4. **complete** — the rebuilt synthetic projection can be read subject to the
   existing actor/target fence and Query authorization. No later live event is
   claimed to be processed until its own normal job commits.

Late live facts below the scan cursor are not lost: the existing atomic Collector
fanout creates live jobs, and final coverage includes them. Unsupported legacy
writers that bypass fanout are not covered by that guarantee. A quarantined fact
without a receipt prevents completion; it is never silently acknowledged or
exposed as a complete projection. A new replay generation may explicitly retry a
quarantine from an earlier generation, again with bounded worker attempts.
Dirty scoped reads fail closed when a requested course has either a result row
or raw facts. A request for course A cannot distinguish no result from data or
replay belonging only to B; tombstoned target remains absent, and a deleted actor
is unavailable.

Replay creates/reopens at most 16 jobs per step. The singleton admission guard
caps all outstanding jobs, including quarantine, and limits outstanding replay
jobs to half the global budget, preserving the other half for live work. A
quarantined live job converted to replay consumes a replay slot but does not add
another global outstanding job. Processing existing jobs can drain capacity
while scan is active. `work_class` keeps live work first within a partition;
after at most three live-only claims, replay is selected first when eligible.
Candidate cursor fairness between subjects remains the CORP-81 bounded scheduler
hint, not a global FIFO guarantee. At critical logical DB size, a new replay
request or new/reopened replay work is refused; same-ID reconciliation remains
available. The database-size check is not a filesystem/WAL hard limit.

The extra replay history, receipts and indexes are durable technical state; no
fact TTL or automatic deletion is introduced. Privacy must delete replay state,
jobs, receipts and results under the same subject lock before its own completion
ACK. The final anti-join uses an indexed subject/event cursor and is transaction
timeout-bounded, but it still depends on the subject's total history: large-data
completion time and load have not been measured. `a005` adds ordinary indexes
inside the migration transaction; use a coordinated migration window and
bounded lock timeout, and reconcile an unknown migration outcome.

Before enabling replay, `a005` validates composite foreign keys binding each
receipt and job's `(producer_namespace, event_id, subject_ref)` to the owning
fact. Historical cross-subject mismatches fail the entire migration closed at
`a004`; no receipt, job or projection is silently moved or deleted. Investigate
and reconcile such data explicitly before retrying. New mismatched rows are
rejected. Cleanup additionally joins to the owning fact, and scan/coverage
require matching subjects; they cannot delete another lifecycle's receipt under
the wrong subject lock.

## Compatibility gate

Before the first replay begins, drain and stop **all pre-85 Processor and Query
processes**, migrate, then start versions that understand replay dirty state and
the new job fields. An old Processor can interfere with cleanup and an old Query
can expose a partial result. Application rollback to those versions is unsafe
while any replay is active/dirty. A compatible existing Collector can remain
running because new job columns have live defaults and its fact/job fanout is
atomic; this source compatibility is not a mixed-version runtime PASS. No
production rollout, actual privacy barrier or active Query API is claimed.

## Actual checks

`python -B tools/test_replay.py` uses newly generated synthetic PostgreSQL 16
databases and owner/sentinel-checked cleanup. The suite covers corrupt projection
rebuild against independent count/fingerprint oracles; page limits and failure
before/after checkpoint commit; repeated IDs; old claim fencing; late live facts
and a concurrent arrival; deleted subjects; independent consumers; reserved
capacity including quarantine; pre-existing versus current-generation poison;
per-partition live/replay fairness; scoped dirty no-data; cleanup function rights
and exact subject lock; and `a004 → a005` preservation. The existing Processor,
Collector and backpressure PostgreSQL suites are separate regression evidence.
Synthetic corrupted `a004` cases in both receipt mismatch directions and a job
mismatch verify migration rollback without data loss; new mismatches are rejected.
No K3s, production deletion/restore or performance/SLO result is implied.
