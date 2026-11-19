# Synthetic processor storage (CORP-81)

`PostgresProcessor(Database)` implements `ProcessorStore`: `claim(consumer,
version, worker_uuid, limit, lease_seconds)` returns `ClaimedBatch | None`;
`commit(batch, synthetic_delta(batch.facts))`, `fail(batch)`, `renew(batch,
lease_seconds)` and `release(batch)` complete the lifecycle. The two supported
version-1 consumers are `synthetic.activity` and `synthetic.independent`. Their
identities are independent of worker replicas. These effects count synthetic
facts and use an order-independent XOR fingerprint; they are not activity time,
learning progress or product KPIs. Runtime loop, handlers and privacy restore
barrier are separate work.

Candidate discovery is nonlocking. Tombstoned subjects are filtered before the
32-candidate limit and the fence is checked again under the subject lock. A
local per-consumer/version cursor advances across attempted candidates and
wraps after exhaustion, so repeatedly busy early subjects do not permanently
hide later candidates. The cursor is a scheduler hint, not a durable checkpoint;
each call remains time-bounded and examines at most 32 candidates. For each candidate the store attempts the
shared `subject_lock_key` exclusive advisory lock first, then takes the
consumer/version/subject lease row with `FOR UPDATE SKIP LOCKED`. It never holds
a partition lease while waiting for a subject. A lease records worker UUID,
generation, expiry, claim UUID and subject epoch. Generation increases on every
new claim. Commit, failure, renewal and release take the subject lock first and
verify fence, owner, generation, batch and DB-clock expiry before changing
state. Commit checks expiry again just before transaction exit; its row lock
serializes a competing takeover until the commit resolves. This is a fenced
transaction boundary, not a guarantee that network response time precedes the
wall-clock expiry.

The claim stores exact job membership and snapshots at most 100 facts, with a
limit of 8192 JSON bytes per fact; a malformed or oversized fact is quarantined
without ACK. New arrivals during computation remain pending. Computation is
outside the transaction; commit verifies the supplied delta against the claimed
records and rechecks DB membership, hash and applied flags. The same transaction
inserts durable per-consumer/fact receipts, updates one generic subject result,
marks exactly the claimed jobs completed and releases the lease. Completed-job
compaction and recreation cannot apply a fact twice while the receipt remains.
Receipts are technical deduplication state, not raw payload copies. Their storage
growth, backup and privacy deletion must be planned and measured; they cannot be
discarded merely because completed jobs are compacted. No downstream jobs are
created in this version, so the admission capacity counter is not reopened.

Explicit handler failure increments each claimed job's attempt count and uses
bounded exponential backoff plus deterministic jitter. On a subsequent claim,
an expired lease likewise consumes an attempt and returns its jobs to pending
after backoff. Three failures/expiries quarantine the job without acknowledging
it. One batch failure affects the entire claimed batch; handlers that require
finer isolation should claim a smaller batch. A graceful release returns jobs
to pending without increasing attempts. Crash before commit leaves the claim
recoverable after expiry; a lost response after commit is reconciled from job and
receipt state. Replaying the old `commit` raises `StaleClaim`, never a second
effect. Quarantine remains outstanding for the CORP-80 capacity limit.

Limits: lease 1–60 seconds, batch 1–100, at most 32 candidate subjects per claim
call, and one stored synthetic result with at most 1000 distinct course IDs per
consumer/version/subject. If a result would exceed that bound, commit fails
without effects and the worker can apply the normal retry/quarantine policy.
These are engineering limits, not measured throughput. Database transaction
timeouts bound each operation and the complete candidate scan. Cursor fairness
under sustained load and replica churn is not measured.

## Bounded projection read

`SyntheticCountsReader.read_counts(consumer, version, actor, subject,
course_ids)` accepts one target subject and 1–100 distinct, explicitly selected
course IDs. `PostgresSyntheticCounts` takes shared subject locks for actor and
target in deterministic UUID order, checks both fences inside the same bounded
transaction, and returns a `SyntheticCountSnapshot` with only requested course
counts. It does not expose overall fingerprint or total coverage. Deleted actor
or a projection with an outdated target epoch raises `ProjectionUnavailable`;
a deleted target or absent result returns `None`. Caller must first authorize
the verified actor, target and every course. A result can lag pending jobs.
`as_of` is the latest `received_at` among processed fact receipts for only the
requested courses, or `None` if none contributed; it is not a materialization
timestamp, freshness watermark or completeness proof. Processing other courses
does not change it. The Query HTTP endpoint and scope policy remain future work.

Privacy must acquire the same subject lock exclusively before fencing/deleting,
and remove that subject's jobs, receipts, results and leases before ACK. The
bounded shared reader locks make deletion wait for an already materializing read;
no bytes already returned to a client can be recalled. A copied/restored local
fence is not an authentic restore barrier. The processor cannot update raw
activity payloads; Query can only select technical results and cannot mutate them.

`a004` is expand-only and preserves `a003` facts, jobs and admission budget.
Rollback to an old active processor is unsupported: it cannot enforce the new
lease/receipt protocol. The schema is forward-only. No production migration or
deployment has been performed.

## Actual checks

`python -B tools/test_processor.py` uses fresh generated databases on the
synthetic PostgreSQL 16 endpoint and verifies sentinel/owner before cleanup. It
checks two store instances, independent consumers and subjects, lease takeover,
expiry immediately before commit, stale fencing, new arrivals during computation,
before/after-commit failure, membership tampering, completion compaction/replay,
bounded retry/quarantine and independent work, invalid/oversized quarantine,
actor/target privacy fencing, query role restrictions and `a003 → a004` with
pre-existing facts/jobs/budget. Counts and fingerprint are checked against an
independent hand-derived oracle. Tests do not prove K3s, throughput, p95, an
authentic restore barrier, full deletion protocol or production readiness.
