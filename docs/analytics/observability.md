# Private operational metrics and local reconciliation

Each application exposes `/metrics` on its private health port. Public ingress
admits only the events endpoint and reports subtree, so this route must remain
denied externally. Collector metrics contain only fixed outcome labels, never
subject, course, event, token, URL, SQL or arbitrary exception labels.

`analytics_collector_ack_events_total` counts the five event statuses in server
ACK responses. `analytics_collector_requests_total` distinguishes invalid,
unauthorized, forbidden, overloaded and unavailable requests. These are
process-local counters and reset with the process. A lost or partial response
may follow a committed fact without a counted accepted ACK. They are not a
lifetime durable ledger; use stored facts and reconciliation for that question.

Processor adds a bounded read-only aggregate snapshot: stored facts, fixed
consumer/version/job states, unknown-state totals, oldest pending age, retries,
quarantine, active replays, database bytes/connections and applied privacy cursor.
`analytics_diagnostics_available=0` means the diagnostic query failed or timed
out; missing values have a separate `_known` flag and are not rendered as zero.
Diagnostics remain available when the privacy gate is closed, but cannot open
the gate. Only Processor receives these privileges.

All roles export their current privacy readiness, runtime uptime and process CPU
time. Linux process RSS is read from its own bounded `/proc/self/statm`; on an
unsupported platform its `_known` flag is0. These are process observations, not
node utilization or quota measurements. Each role also reports its own pool size,
checked-in, checked-out, configured maximum and active overflow with fixed role
labels. Each gauge has a `_known` flag; an unsupported pool or unlimited maximum
is unknown. Overflow below zero indicates unallocated pool capacity and is
reported as zero active overflow. Values are instantaneous, not an atomic snapshot.
Processor counts waiting locks on sessions connected to the current database,
including transaction-ID waits; it exports only the aggregate. A failed diagnostic
read leaves the lock count unknown.

[Resource snapshots](resource-observability.md)
separately inspect the designated database container, filesystem and WAL.

One metrics scrape per process is allowed at a time. Probe and diagnostic reads
each have at most a one-second deadline (or the shorter role DB deadline).
Response ACK instrumentation retains at most64KiB and100 item statuses; malformed
instrumentation increments a fixed error counter and never changes the API ACK.

Use the [quality CLI](quality.md) to distinguish local `no_data`, scoped synthetic
`zero`, `stale`, `mismatch`, `deleted` and `unavailable`. Its per-fact receipts are
the checkpoint. `data_through` is the maximum processed server timestamp, not an
ordered delivery watermark. Upstream/catalog completeness remains unknown.
The CLI is a restricted synthetic maintenance tool, not a user report.

## Alert rules and actions

These are prepared operational rules. No external alert delivery or monitoring
stack has been deployed by this work.

| Signal | Interpretation | Action |
| --- | --- | --- |
| Privacy readiness0 or source observation stale | Deletion barrier closed | Keep data operations closed; check independent authority and applied prefix. Do not force readiness or copy an old watermark. |
| Unavailable/overloaded requests rise, stored facts stop | Admission/storage failure | Inspect logical/physical storage and shared limits; retain the same event IDs for retry. Do not delete learning history to free space. |
| Pending age or quarantine rises while stored facts grow | Processor behind or failed work | Inspect consumer/version and fencing errors; reconcile, then explicitly replay within its budget. |
| Local reconciliation mismatch | Ledger/result inconsistency | Keep affected projection dirty; run bounded rebuild and verify coverage before reopening. |
| Oldest pending age unknown | No pending jobs, or diagnostics unavailable | Check `_known` and diagnostics availability before interpreting as zero lag. |
| Database critical size or low filesystem space | Storage saturation | Stop new work according to the admission contract, preserve verified backups and increase capacity. |
| Backup/restore verification fails | Recoverability not established | Keep prior verified copies; investigate before promotion or pruning. |

Thresholds and sustained windows for load/latency, WAL growth and resource alerts
must be measured by CORP-114/115/116. A point-in-time resource snapshot is not a
load test, improvement claim or accepted SLO.
