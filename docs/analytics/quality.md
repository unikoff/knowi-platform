# Synthetic reconciliation and diagnostics

`PostgresReconciler(database).check(consumer, version, subject, *, course_id=None)`
checks one entire local subject ledger for either version-1 synthetic consumer.
It is an operator maintenance port, not a browser report or an authorization
check. It takes the shared subject lock in one timeout-bounded transaction, scans
facts in keyset pages of 100, validates the accepted event and canonical hash,
and compares owner-coherent receipts and jobs with an independent synthetic
count and fingerprint fold. It compares the fold with the stored result.
Completed jobs may be compacted when their receipts remain. No facts expire.
The scan is not capped by a fact count, but a large history may exceed the
transaction deadline: then the result is `unavailable` without partial counts.
A long scan can delay deletion while it holds the shared subject lock; its
duration and large-data load have not been measured.

The report uses `checkpoint_kind=per_fact_receipts`, not an ordered offset.
`coverage` counts local facts, receipts, pending, claimed and quarantined jobs,
missing jobs and invalid facts. `scan_complete` means the local scan finished,
not that all jobs succeeded or external delivery is complete. `data_through` is
the maximum server `received_at` of a processed fact in the requested course
scope, or all courses when no filter is given. It is not a commit-order or
completeness watermark. `projection_version=synthetic-count-v1` identifies this
synthetic fold. `external_coverage=unknown` always: neither the source delivery
nor the course catalog has been verified.

* `no_data`: the subject's local fact ledger is empty.
* `ok`: local facts, processed receipts and the stored result agree.
* `zero`: an explicitly requested course has no facts in a fully scanned,
  locally consistent, nonempty subject ledger. This is only a synthetic event
  count; it does not prove a real course exists, its complete delivery, or zero
  learning activity.
* `stale`: replay is dirty, a job is pending, claimed or quarantined, or the
  stored result belongs to an old subject epoch. A finished scan may still have
  this status; do not treat it as zero or complete.
* `mismatch`: local fact, ownership, receipt/job coverage or result integrity
  differs. `invalid_facts` prevents an expected count from being reported.
* `deleted`: the subject is tombstoned; counts and coverage are withheld.
* `unavailable`: privacy or storage is closed/unavailable, the transaction
  times out, or a memory bound is exceeded; partial counts are withheld.

The port honors the supplied `Database.transaction_guard` at transaction entry
and exit. A non-synthetic caller must supply the live, verified authority guard
and a separate authorization design; this maintenance port does not establish
either. In particular, do not use it in a browser report without scoped grants
and catalog checks. Existing Query actor/target privacy guarantees remain
separate.

## Processor diagnostics

`PostgresDiagnostics(database).read()` is a fixed aggregate, read-only snapshot
for private Processor operations. It deliberately uses a separate read-only
transaction without the data privacy readiness guard, so it can diagnose a
closed gate. It exposes no subject, event, course, arbitrary label, SQL or raw
payload. It returns stored fact count (not lifetime accepted count), the fixed
two known consumer/version combinations by the four known job states, aggregate
unknown consumer and unknown status counts, oldest pending job age in seconds
(`null` if absent), retrying pending/claimed jobs, quarantined jobs, active
replays, logical database bytes, current database connection count and the
applied privacy cursor. Unknown consumer and unknown status counts can overlap.
The cursor is an observation, not proof that an authority is attached or that
readiness is open. A failed or timed out read raises `DiagnosticUnavailable`.
The database-size value does not include a hard limit on WAL or filesystem use.

## Guarded local synthetic operator command

From `services/analytics`, run `python -B tools/reconcile_synthetic.py
--consumer synthetic.activity --subject-ref <uuid>` for a read-only check;
`--course-id <id>` narrows only the reported synthetic count. The command
validates the runtime's synthetic marker, owner, sentinel, database identity and
current migration head and takes the shared local operation lock. It uses the
Processor role and a local synthetic database; it does not authenticate a live
privacy authority. Output contains no subject, event or request IDs, raw data
or secrets. A successful check command can return `stale` or `mismatch` in
its JSON; inspect `status`, not only the exit code.

Repair requires an explicit whole-subject `--repair begin|step|finish` and
`--request-id <uuid>`; continuation steps additionally require the generation
and subject epoch from the prior result. A step has a maximum `--limit 100`.
Each invocation performs one bounded operation; there is no automatic loop or
retry. A timeout or lost reply has an unknown outcome: reconcile using the same
request ID before deciding the next operation. Follow the phase and capacity
rules in [replay.md](replay.md). Before the first begin, drain and stop all
pre-85 Processor **and Query** processes; the begin command checks the existing
retirement guard. Rollback to either old process while replay is active or dirty
is unsafe. The command does not certify a mixed-version rollout.

`python -B tools/test_quality_postgres.py` runs real PostgreSQL tests only on
new synthetic UUID databases with guarded cleanup. These verify local behavior,
not source delivery, production recovery, performance or an SLO.
