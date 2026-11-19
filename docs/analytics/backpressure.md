# Synthetic Collector backpressure (CORP-80)

Revision `a003` adds a singleton DB-owned admission policy and per-subject
technical window counters. Collector replicas share the singleton row lock, so
process restarts or additional replicas cannot reset/independently multiply the
budget. The owner may change policy columns; Collector can update only the global
window/count, not the limits. The collector's per-subject rows are restricted to
opaque lifecycle UUID, UTC window and count. No real user data or Core producer
is introduced by this synthetic implementation.

The default engineering limits are **60 newly accepted browser facts per subject
per UTC minute**, **600 globally per UTC minute**, **10,000 outstanding jobs**,
and a **512 MiB logical database budget**. Size warning starts at 80%, critical
rejection at 90%, leaving 10% as a reserve. A size warning is logged without PII,
at most once per minute per process. These limits are not measured SLOs. The load
profile models 2.4 batch requests/second with burst 36; the rate defaults admit
that modeled single-event burst, subject to DB/queue capacity. A legal 100-item
envelope may exceed the per-subject minute limit and receive 429 after a partial
commit; the client retries the exact same IDs. Fixed windows permit a burst at
both sides of a minute boundary; a backwards DB clock correction does not reopen
an older window. Replica policies come from the database, not local settings.

The subject fence lock comes first, followed by a separate versioned event-key
advisory lock and indexed fact/hash lookup. A deleted lifecycle is denied.
Existing matching facts with all required jobs return duplicate even
when rate, queue or disk caps are reached, and consume no new quota. A conflict
does not consume quota. An old fact whose required jobs are missing must first
repair those jobs: repair takes the capacity guard, adds no rate charge, and
can receive 429 if there is no capacity. A previously unknown identity is
validated for clock policy, then acquires the singleton guard; only an allowed
new fact increments both counters and is inserted with both synthetic jobs.
Rejecting a new identity does not attempt a raw fact INSERT. If an unsupported
legacy writer bypasses the event lock and wins the subsequent INSERT race, the
new transaction rolls back its counters and returns 503 for same-ID retry. A
failure, cancellation or timeout before commit rolls all those changes back.
Timeout or 429 after a partial batch does not identify the committed items;
retry the same payloads and IDs with bounded backoff/jitter. `Retry-After` is
1–60 seconds. Existing facts are never deleted to free capacity.

Outstanding means every job whose status is not `completed`, including claimed
or quarantined jobs. The count uses a bounded indexed query and admission rejects
when the new jobs would exceed the cap. Completion allows later admissions; it
does not remove acknowledged facts. Future Processor implementations that create
or reopen jobs must use the same singleton guard and capacity rule. The size
probe uses `pg_database_size(current_database())` under the collector role. It is
a sampled logical DB threshold, not a filesystem or WAL hard limit: page
allocation, unsupported concurrent writers and WAL can overshoot it. A separate
filesystem/WAL reserve, monitoring and operator response are required. A failed
size/DB probe fails closed as 503. At warning investigate free disk, DB and WAL;
at critical stop incoming browser admissions, restore capacity or safely drain
workers; never delete facts as an automatic remedy.

At each successful new admission at most 64 subject-window rows older than the
previous window are deleted using `SKIP LOCKED`. These are technical rate rows,
not facts, fences or jobs. An idle service does not run a timer: old rows can
remain until later admissions or separately authorized maintenance. Privacy can
delete the subject's technical rate row; it must follow the subject-lock ordering
of the deletion protocol. Rate admission itself is not a replacement for the
unimplemented privacy restore barrier or for the reserved privacy/control lane.

`a003` is expand-only, retains old facts/jobs, and has no downgrade. Old Collector
code can write without the new guard, so quiesce old writers before activating
the new policy and prevent rollback to active old writes without reconciliation.
Per-process 4-request default, body limits, pool, transaction deadlines and PG
budget are separate layers. The global budgets count accepted browser facts,
not invalid signatures or rejected requests: edge request/signature abuse limits,
actual memory/load testing and privacy-reserved capacity are not established.
Future Core outbox traffic is not implemented or dropped by these browser quotas;
the 24-hour figure in CORP-113 remains a sizing model, not achieved autonomy.

## Verification

`python -B tools/test_backpressure.py` creates fresh guarded synthetic PostgreSQL
databases, never the retained runtime DB. Tests exercise two collector instances,
subject/global caps, duplicate/conflict at cap, fixed-window reset/backward
clock, invalid-clock rollback, injected SQL/timeout/probe failures, a real DB
connection failure, pending backlog and drain, size warning/critical thresholds,
bounded technical cleanup, role privileges and a signed HTTP partial-batch 429
with exact-ID retry. Privacy/readiness/catalog in HTTP tests are synthetic stubs;
they do not prove the production privacy protocol. Threshold tests use small
limits and do not fill the host disk. No throughput, p95 or K3s claim follows.
