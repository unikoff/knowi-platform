# Synthetic activity benchmark

The accepted inputs are [profile v1](../../services/analytics/bench/profile-v1.json) and [load profile](load-profile.md). The [execution log](EXECUTION.md) records actual runs and their limitations. The harness runs one stage and one replica configuration per invocation. Each invocation creates a new synthetic database and private run directory. Repeat three times for each approved configuration. The harness opens connections only to the synthetic Analytics target and sends no notifications.

The dry plan opens no processes, services or DB and writes no output file:

```powershell
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/benchmark_activity.py `
  --profile services/analytics/bench/profile-v1.json --stage 50 --replicas 2,1,1 `
  --seed 11320261116 --output services/analytics/.local/benchmark-results/stage50-211-1.json
```

Before any `--run`, root must approve the current resource preflight and specify minimum free physical memory and disk in MiB. Only then set `ANALYTICS_BENCHMARK_GO=1`, `ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB`, and `ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB` for that command, and append `--run`. There is no default floor or automatic permission. The harness rejects non-Windows, non-3.12, unknown profile/replica configuration, non-new output and missing GO/floors. The output is a new JSON file directly under `services/analytics/.local/benchmark-results/`; it never replaces an existing file.

Set `ANALYTICS_BENCHMARK_MIN_FREE_COMMIT_MIB` to a positive MiB value when
available Windows commit is a constraint. The same preflight and periodic
checks then stop on low commit even when physical memory is above its floor.
The artifact includes the configured floor and lowest observed commit.

For matched cadence only, `--benchmark-postgres` reads the private manifest
`services/analytics/.local/benchmark-wsl-postgres.json`. The approved
sprint test image on Windows disk F uses `127.0.0.1:62333` with project
`knowi-analytics-s16-benchmark-f`. Both sides of a comparison must use the
same disk-backed PostgreSQL instance and settings. Before admission, verify
the target, `fsync`, `synchronous_commit`, `full_page_writes`, and a few
committed writes. The owner record includes the selected port. New databases
still require a random ID, exact owner and sentinel. The default target
stays on port 57392.

For a matched 50-learner diagnostic on a014, use `--stage 50 --matched-cadence 60 --matched-schema a014 --activity-poll-ms 50 --query-pool-size 4`. Use `--matched-smoke` for a 60-second warmup and a 120-second measurement. For a longer load measurement, omit `--matched-smoke`. The a014 scenario enables the day and course projections and the Processor boundary credit worker.

Matched results record table and index footprints at both measurement boundaries alongside database-wide size and cumulative statement statistics. The live-row counts are statistics estimates. `matched_ack_to_admin` measures from the first accepted ACK to the successful admin response on common minute ticks. It includes the rest of the batch and the query. The final oracle separately checks that the measured inputs agree with the report.

In the matched profile, warmup queries read from the workload start so the
queries exercise existing data before the measurement begins. The measured
queries and final verification begin at the measurement boundary. Both
variants keep the same schedule and report scopes. CPU deltas cover the
owned application processes. PostgreSQL CPU in WSL is outside that sample.

A run creates an Analytics-only UUID-named disposable database through the existing cross-service owner/sentinel mechanism and applies the canonical chain through a010 **inside that disposable DB only**. It checks the database name, owner, sentinel, role grants, empty facts/jobs/fences and reasonable DB clock. The migrate owner begins a fresh dataset generation; the same run UUID is passed to every owned Processor and Query with a010 projection enabled. The Processor performs actual backfill and bucket materialization, and Query reads that projection. Startup waits for readiness; final oracle verification separately waits for the bounded projection queue to drain. A dedicated fresh fixture authority, grant issuer, catalog, courses and lifecycle UUIDs are created per run. Collector, processor, query and privacy run under the existing Windows kill-on-close Job; listener ownership and privacy readiness/trust fingerprint are checked before traffic. Keys, reader secret, logs and owner record stay in the private run directory and are not printed or placed in the result artifact. No inherited `ANALYTICS_*` or Core environment is passed to children.

Collector and Query traffic use the actual endpoints and real Ed25519 grants refreshed per request with a 50-second lifetime. A fixed model start of 2026-01-01 is translated to the actual start without changing intervals or touching the server clock. There are 300 seconds of warmup followed by **900 seconds of measurement**, with 15-second segments; the result records planned and actual durations, translation, active/overlap mix and schedule lag. It records opening, warmup and measurement ACKs separately, request counts by role/status, 429 `Retry-After`, errors/timeouts, successful p50/p95, configured replicas, CPU/pool/DB metrics, and independently computed `activity-v1` oracle parity for accepted facts. Health checks every 15 seconds and metrics every 60 seconds add separately described operational traffic. `quality=unknown` remains unknown; matching an observed bounded snapshot is not proof of complete source coverage.

The process checks free physical/pagefile and workspace disk before provisioning and every 15 seconds; it stops on supplied memory/disk floors, missing privacy/readiness/trust or metrics, unowned listener, clock shift, unknown outcome, excess DB connections or quarantined jobs. Any Query 503 is recorded and stops the run without retry: the response alone cannot distinguish a dirty projection from a privacy failure. A measured error is FAIL; an early guard stop without an observed violation leaves the incomplete run UNRUN. The operator must independently monitor Core responsiveness and swap thrashing; the harness cannot infer either from free memory. A timeout or lost reply is an unknown outcome: the harness stops without resending and keeps the database and owner record for reconciliation. It stops owned processes but intentionally does not delete the disposable DB or private directory on success or failure. Cleanup is a separate scoped operator step after reconciling exact owner/sentinel and retaining required evidence.

A separate `--run --correctness` invocation uses a different fresh synthetic DB and verifies an old (within seven days) observation, overlapping courses, an unobserved gap, duplicate ACK, conflicting ACK and report/oracle parity. It is deliberately short, has no load duration and does not test actual browser hide/sleep tracking. Its result cannot be a load PASS. The ordinary load run does **not** execute that separate scenario, live replay, privacy load, deletion barrier, backup/restore, or lease-takeover scenarios; it does not prove privacy lag or an eligible-live-job watermark. Those fields remain `UNRUN`, even if the measured Collector/Query samples and parity pass. One run also cannot establish the required three independent repetitions or overall stage acceptance. An incomplete run is `UNRUN`; observed threshold/correctness violations are `FAIL`. Never label a shortened run as a 900-second PASS or infer production capacity from this test.

Offline checks: `python -B -m pytest -p no:cacheprovider services/analytics/tests/benchmark -q`. Their virtual clock and fake HTTP are test substitutes only and are not benchmark results.


## Separate mixed and recovery modes

With the same GO and resource floors, `--run --mixed` retains the full 300-second warmup and 900-second measurement. It seeds three extra synthetic subjects before warmup, starts a separate privacy-guarded replay client after warmup, and runs the existing `synthetic.activity` replay with at most one fact per two seconds. It then submits three separately signed deletion controls for those extra subjects, measuring journal-to-complete-purge times. After live traffic it checks the replay subject's synthetic count against its stored fact count. The extra three seed events and deletion requests are reported separately from the profile's base traffic. This is not a full privacy-throughput experiment or a test of a future time-projection consumer. If any readiness/privacy check fails or an outcome is unknown, the run stops without retrying the mutation and retains the owned DB.

With the same GO and floors, `--run --recovery` is a short, separate scenario in fresh owned databases. It seeds four browser segments, measures the same bounded synthetic replay and confirms the same request ID and count/fingerprint before and after. It submits three signed deletions and waits for journal receipt, tombstone and removal of local subject data including bucket projections. A bounded in-memory logical snapshot of one subject's facts and pre-delete privacy cursor is transplanted into a second newly created sentinel/owner-verified DB. A fresh restored dataset generation is begun and a real Processor rebuilds it. The restored query must remain unready until a separate privacy worker replays the independent journal and the projection backfill completes, then become ready while the deleted user remains forbidden. This proves neither backup completeness nor backup RPO/RTO. Both modes record their actual scope and leave unrelated/full-profile acceptance `UNRUN`.

## Month-window capacity checks

These separate opt-in PG tests are not the 900-second load profile. Run each only in its own root-approved resource window, after confirming the current database fixture and free memory/disk. The fixture creates a new owner/sentinel-verified database, applies the canonical chain, and cleans it at exit; do not aim it at an existing database. The sparse test uses real admitted synthetic facts and the Processor store; the dense test inserts synthetic ready buckets and tests Query only.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='services/analytics'
$env:PYTHONTZPATH='C:\Program Files\Git\mingw64\share\zoneinfo'
$env:ANALYTICS_RUN_ACTIVITY_PROJECTION_SYNTHETIC='1'
$env:ANALYTICS_RUN_ACTIVITY_MONTH_SAMPLE='1'
services/analytics/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider `
  services/analytics/tests/activity_projection/test_projection_reports_pg.py::test_sparse_31day_50subject_explain -q -s --tb=short
```

Only after a separate dense GO and explicit resource floors in MiB:

```powershell
$env:ANALYTICS_RUN_ACTIVITY_MONTH_DENSE='1'
$env:ANALYTICS_BENCHMARK_GO='1'
$env:ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB='<approved positive integer>'
$env:ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB='<approved positive integer>'
services/analytics/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider `
  services/analytics/tests/activity_projection/test_projection_reports_pg.py::test_dense_31day_50subject_query_only_explain -q -s --tb=short
```

The dense command also requires the common environment variables from the preceding block; it checks the supplied resource floors throughout seeding and measurement. Neither month test proves source completeness, real privacy authority, or production capacity.
