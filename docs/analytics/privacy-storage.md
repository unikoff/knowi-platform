# Synthetic privacy storage and applied journal prefix (CORP-427 B)

`PostgresPrivacyStore(Database)` uses a dedicated privacy-role pool.
`cursor(authority_id)` returns `AppliedCursor(authority_id, applied_through)`;
the first call binds the database to that authority and later changes are
rejected. `apply(authority_id, JournalEntry, limit=100)` returns
`DeletionProgress`; each page has a limit of 1–100. It is complete only when
every local obligation is gone and the applied prefix has advanced. A repeat of
the same sequence, control ID and canonical hash resumes a partially completed
deletion or returns the existing completion; reuse with a different identity or
hash conflicts. A gap is rejected. The control hash covers producer and the
versioned DeletionFence; the authority-assigned sequence and received time are
not part of that hash. Sequence and epoch must fit a positive PostgreSQL signed
64-bit integer; an out-of-range journal entry fails closed.

`JournalEntry` validation and hashing do **not** authenticate its source. Only a
caller holding a genuinely verified, current `VerifiedFrame` from the independent
authority may bind its authority ID or submit its entries. The caller owns
signature verification, nonce/session binding, continuous page order, fresh
opening barrier and live stream attachment. A cursor copied from a restored
database is not proof of authority freshness. The privacy storage adapter has no
boolean or header that can assert trust and cannot open application readiness.

The store serializes entries with a dedicated global transaction advisory lock,
then takes the common exclusive subject lock. The first page commits a permanent
tombstone at `max(existing_epoch, requested_epoch)` and a `purging` receipt;
this immediately invalidates all old claims even if their lease rows remain for
later pages. Under that fence each transaction deletes at most `limit` fact rows
and at most `limit` rows from each of results, leases, replay history and
admission windows. Validated composite foreign keys cascade facts to their jobs
and processing receipts. A page of facts can cascade to more than `limit` child
rows; the page limit is not a bound on every physical delete or on elapsed time.
The Database transaction timeout is the operational bound; large histories and
worst-case fanout require measurement. No automatic fact TTL is introduced.

The local ACK and cursor update are atomic and occur only after explicit checks
show no remaining facts, jobs, processing receipts, results, leases, replay rows
or admission window for that subject. Tombstones, sequence receipts and the
applied prefix remain as minimal durable state with no automatic age TTL.
The database also rejects tombstone reversal, subject UUID reassignment and epoch
decrease; the privacy role has no permission to delete a subject fence.
Privacy control has reserved role/pool ownership; runtime Collector, Processor and
Query can read only the state identity/prefix columns and cannot write them or
read control receipts. `PostgresSubjectFence.permits(subject)` is an early
shared-lock admission check, not a substitute for the final transaction fence.
There is no report cache today. Any future cache, projection or downstream store
must add its own scoped deletion and acknowledgement obligation before this ACK
can honestly represent full erasure.

`a006` adds the local state/receipt tables and subject indexes. It does not
modify the independently durable authority journal or any Core database. The
migration is forward-only; rolling back application code does not roll back
deletion or resurrect data. A restored Analytics snapshot must remain closed
until the independently authenticated, continuous deletion journal has been
reapplied through the current opening barrier and attached stream. Actual backup
restore and the full readiness gate are separate acceptance work.

## Actual local checks

`python -B tools/test_privacy_postgres.py` uses only fresh generated synthetic
PostgreSQL 16 databases with sentinel/owner-checked cleanup. It covers deletion
before collection, a new opaque lifecycle, duplicate and conflicting control
IDs, gaps, immutable authority binding and role grants; a crash after the fence
and partial purge followed by a new pool resuming the same entry; lost final
reply; global ordering of two simultaneous entries; stale worker and replay
handles; and actor/target shared reads draining before deletion ACK. The test
checks all seven current subject-owned physical table families and retains only
the minimal tombstone/control receipt. The five-fact crash/restart fixture took
0.234 seconds against its 60-second test bound; this single local observation is
not a service SLO or load result. It does not prove authenticated source
integration, restore, Core deletion, external caches, K3s or production safety.
