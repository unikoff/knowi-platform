# Activity bucket projection (a010)

The active source migration head is a010. Promotion of the tested files did not
migrate retained databases or deploy an image. The projection is off by default.
Use the same externally controlled dataset UUID for Query and Processor via
`ANALYTICS_ACTIVITY_PROJECTION_RUN_ID`, and enable separately with
`ANALYTICS_QUERY_ACTIVITY_PROJECTION_ENABLED=true` and
`ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED=true`. A verified trust
manifest and privacy authority are required. The read role stays unavailable
until the selected generation has completed its scan and drain.

One subject/15-minute UTC bucket covers all courses and devices. The trigger
marks one or two dirty buckets atomically with a browser fact; a full pending
budget rejects the containing intake transaction as unavailable. Repeated
identical facts do not add a second contribution. A worker claims briefly,
loads under the subject fence, computes without a database lock and publishes
only if the generation, privacy epoch, lease and dataset UUID still match.
Expired leases are recoverable; invalid or repeatedly failing buckets are
quarantined and make affected reports unavailable. An explicit bounded replay
may retry one unchanged quarantined bucket. The original raw fact is retained.

The scan has a durable cursor and a drain phase. On restore, an external
controller must issue a new dataset UUID outside the restored database and
invoke the migrator-only compare-and-swap `begin_activity_projection_backfill`
with the old and new UUIDs, then run a full scan and drain. All replicas of a
dataset use the same UUID; ordinary rollouts retain it. Processor and Query
never replace a mismatching UUID. Reusing the old external UUID after a
restore defeats this guard and is prohibited. Disabling this code is not a
rollback of data or triggers.

The opt-in `history_revisions=true` activity report returns validators for the
28 closed Moscow days from today minus 29 through today minus 2. It is limited
to a learner's own authorized course and whole-day query bounds. A null token
means no observed daily row in the requested interval, not zero or complete
coverage. Dirty or invalid projections fail the report instead of returning a
validator. Tokens bind the observed daily values to the subject, course, run
UUID and PostgreSQL database OID/name and postmaster start time read in the
same guarded statement. A logical restore to a fresh database or a restore
with a new postmaster invalidates old tokens; an in-place restore preserving
both identities is not supported for persistent cache validation. PostgreSQL
restarts also invalidate tokens. Clients must not treat tokens as freshness
or completeness guarantees and must discard cached values on null or failure.
The same opt-in response includes `history_generation`, an opaque subject- and
course-scoped hash of the database incarnation and run UUID. It is stable across
ordinary activity changes and can invalidate cached days from other intervals;
it is null when the configured reader cannot prove the incarnation.
These validators do not replace the required external run UUID rotation and
projection rebuild procedure after restore.

`POST /api/analytics/reports/activity/calendar` is a separate self-only sparse
calendar read. After a guarded metadata check it compares the client's generation
and registry tokens, and expands day payloads only for changed or uncached days
and for the two newest days. The registry token also covers a missing source row:
it means no activity was observed in this projection, not complete coverage or
proven inactivity. A new source row changes that token. The registry query reads
source generation and state together with privacy and run metadata, without
accessing the historical projection payload table. Reuse depends on the existing invariant that writers and
projection workers update source generation when they change projected data.
Manual changes to payloads outside that protocol cannot be detected by metadata.
When the day projection reader is disabled, the endpoint returns 501 so callers
can use the existing report. Dirty, inconsistent or unavailable projections fail
closed rather than making cached data authoritative.

The two byte-size columns are PostgreSQL generated columns. A logical row
copy or restore must omit them and let PostgreSQL recompute them from JSONB;
the processor role cannot update them.

Each bucket allows at most 10,000 raw facts and 8 MiB uncompressed input and
output. PostgreSQL derives stored uncompressed byte sizes from UTF-8
`jsonb::text`; Query uses these and checked item counts before expanding edge
intervals. Oversized, dirty or stale sets fail closed. Legacy calendar,
streak and admin daily presence, when both Core legacy reads and a010 are
enabled, union the Core prefix with browser presence on Moscow calendar days;
a browser-only day has `visited_at: null`. The current bucket is clipped at
the database statement time, so future-dated presence is not counted early.
Core coverage does not assert
browser completeness: the additional browser quality is `unknown` and its
`as_of` is an observation time, not a source watermark. The privacy worker
explicitly purges and verifies the new rows before deletion acknowledgement.

The a010 integration tests require `ANALYTICS_RUN_ACTIVITY_PROJECTION_SYNTHETIC=1`
and create a new disposable, sentinel-guarded database; they never target an
existing application database. Month and dense load samples need separate
resource opt-ins. Passing unit tests does not establish PostgreSQL race,
restore, capacity, or production readiness; those require the gated runtime
tests, representative EXPLAIN/latency measurements and independent approval.
