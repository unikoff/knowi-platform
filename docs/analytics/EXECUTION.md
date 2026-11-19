# Analytics execution checkpoint

## 19.11.2026 — Personal statistics period cache

The mounted account keeps independent successful time reports and completion
counts for each exact course and period. The first read for an unseen period
uses the server. Account changes clear the in-memory store and late responses
cannot populate a new account's cache. The week does not depend on the month
report's coverage.

In isolated Chromium rendering the current `CourseStatisticsPanel` from
`127.0.0.1:5173` with synthetic API responses, Week → Month → Week produced
request counts of 1 → 2 → 2 for each of the time report and completed-count
endpoints. The time values were 1 → 2 → 1 minutes and the return had zero
loading placeholders. Both request counts remained unchanged after the
return settled. Evidence is in
`.local/analytics-browser/calendar-repeat/result.json`.

The optional IndexedDB cache holds only confirmed daily activity values from
Moscow dates today−29 through today−2. The UI can display those days pending a
fresh report, with totals and details supplied by the server. Each read uses a
verified Core session and each write checks the same session after Query.
`CourseStatisticsPanel` passed a Chromium test with two tabs, real IndexedDB,
Web Locks and HttpOnly cookies, and synthetic API responses. A held response
from account A was discarded after a login as B. A new history generation
evicted an old day outside the requested week and invalidated the previously
loaded month. Evidence is in
`.local/analytics-browser/calendar-persistent-browser/result.json`.

On disposable PostgreSQL, a full a014 data and schema archive was restored
into a fresh database. Table counts and row digests, the migration revision,
and the projection run ID matched. Query issued different history generation
and day revision tokens. This rehearsal used `--no-owner --no-acl` and did
not cover a restore into the original running database. The test is
`test_a014_full_restore_pg.py`.

The flag is enabled on the existing local 5173 Docker container through its
`.env.development.local`. It survives a container restart. To preserve the
feature during a later recreation, set
`VITE_ANALYTICS_CALENDAR_CACHE_ENABLED=true` when running the web-dev Compose
service. The Compose default stays off. The local database is still a011.
The Chromium suite also passed with the stand's own flag and synthetic API
responses (`.local/analytics-browser/calendar-persistent-browser/result-stand.json`).

The calendar now sends saved revision tokens in one
`POST /api/analytics/reports/activity/calendar` request. Query checks closed
dates against the day source registry and reads activity values for yesterday,
today, and any missing or changed dates. The normal 30-day period reads 30
dates on a cold load and two dates once the 28 closed days are cached.
Confirmed days without recorded activity can be reused as well. A selected
historical range with every revision confirmed needs no activity payload read.

The a011/a014 PostgreSQL tests record the dates passed to the activity
statement. A full a014 restore into a fresh database also verified that the
calendar POST discards old tokens and returns the same daily values. Chromium
covered the cold and warm month, a changed day, a missing cache entry and a
late response from another account with synthetic API responses.

## 19.11.2026 — Query latency remediation

An instrumented control at 50 learners on F: (a012, 60-second events) measured
578 ms Query p95 and 438 ms p50 over 72 requests. The reader portion had 108 ms
p50. Eight requests retried a pending projection. Overlapping Query requests now
share one in-flight readiness check. Each reader still checks privacy and the
dataset generation. Subject locks are acquired in the existing numeric order
with a single PostgreSQL request.

| Short F: run at 50 learners, 1/1/1 | a011, 15-second events | a012, 60-second events |
| --- | ---: | ---: |
| Before remediation | 625 ms | 1047 ms |
| With shared readiness and batched subject locks | 547 ms | 485 ms |
| With shorter early retries | 531 ms | 469 ms |
| With a single Processor readiness transaction and four Query connections | 438 ms | 562 ms |
| Also skipping day claims with pending source buckets and polling at 50 ms | 625 ms | 422 ms |
| With two bucket workers inside the same Processor | 296 ms | 219 ms |

All cases in the table returned HTTP 200 for every measured request and
matched the reference totals. Both profiles met 500 ms in the last short
pair. The short windows leave full capacity acceptance open.
Eighty-three targeted tests passed during the experiment, including
PostgreSQL report parity, ACL, late facts, and fallback. Moving the completed
backfill recheck to a one-second interval did not produce a stable result.
That short pair measured 625 ms and 813 ms respectively. The experiment was
removed before the next run. The change to the day claim passed 14 focused
PostgreSQL tests. Twenty-six tests passed for Processor and role lifecycle.
The last pair used two independent bucket workers inside one Processor with
its existing two-connection limit, Query pool 4 with no overflow and a 50 ms
Processor poll. Case IDs are in `s16-release-20261119-110253-cases.json`.

The Core architecture checker reports zero forbidden imports after moving the
SQL delivery operations to the adapter boundary. The PostgreSQL/Redis source
and configuration gate also passes. The SQLite exceptions cover only named
Analytics components and tools using the independent journal. Core tests now
use disposable PostgreSQL databases, and all 71 migrated scenarios passed.
The preview CI includes this suite and rejects failures or skips. The combined
Analytics regression set passed 154 tests, including the live PostgreSQL
projection cases.

### Full F: repeat after the fixes

Each run used PostgreSQL 16.15 on F:, 1/1/1 replicas, seed 11320261116,
Query pool 4 with no overflow, a two-connection Processor running two bucket
workers, a 50 ms Processor poll, 300 seconds of warmup and 900 seconds of
measurement. PostgreSQL kept fsync, synchronous_commit and full_page_writes on.
All four runs used harness SHA-256
`b09c3a8328342ff996971f37637ab9fc4acf710811974b94480ce4475519aba9`.

| Learners | Schema | Event cadence | Query HTTP 200 | Collector HTTP 200 | Query p95 | Collector p95 | Query target |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 10 | a011 | 15 s | 180 | 480 | 250 ms | 31 ms | Met |
| 10 | a012 | 60 s | 180 | 120 | 141 ms | 31 ms | Met |
| 50 | a011 | 15 s | 540 | 2160 | 578 ms | 16 ms | Exceeded |
| 50 | a012 | 60 s | 540 | 540 | 265 ms | 16 ms | Met |

Every measured request returned HTTP 200 and each oracle matched. The
50-learner minute profile logged 14,496 inserts and updates against 53,412
for the 15-second reference. The historical profile exceeded the 500 ms
target by 78 ms. This comparison has one full window per case. The harness
keeps overall acceptance `UNRUN` for successful load windows because the
separate privacy, replay and recovery scenarios and repeated runs are outside
this comparison.

The case plan is `services/analytics/.local/s16-release-20261119-112133-cases.json`.
Results are in `services/analytics/.local/benchmark-results/`. The four paused
containers resumed, the F: database listener closed, and Web, Core,
Collector and Query returned HTTP 200. No production push or VPS change was
performed.

## 19.11.2026 — checks before the next production push

1. Check the memory reserve and the isolated F-backed PostgreSQL target. Record
   the benchmark fingerprint and pause the owned Docker preview containers for
   the measurement window. Restore the containers when measurements finish.
2. Run the a011 15-second and a012 60-second workloads with 1/1/1 replicas, the
   same seed, and matched windows at 10 and 50 learners. Start with 60 seconds
   of warmup and 120 seconds of measurement. Check the oracle, errors, the
   Collector and Query p95 limits (300 and 500 ms), and freshness. Only a
   completed full measurement window can provide a release capacity result.
3. Run the local Web and Core checks for the learner and course flows, and
   rehearse the a014 raw-fact schema against disposable PostgreSQL. Verify the
   restored preview and check which release flags are active there.
4. Compare the candidate scope, backup and rollback proof, and Jira gates
   against the published manifest. A production decision needs an immutable
   candidate and its own CI, image, restore, and staging evidence.

The staging decision is **NO-GO on Query latency**. Four short measurements on
the dedicated F-backed PostgreSQL 16.15 used the same harness fingerprint,
seed 11320261116, 1/1/1 replicas and 100 ms Processor polling. All HTTP
responses were 200 and the reference totals matched each run. The shortened
windows leave full capacity acceptance `UNRUN`.

| Learners | Event cadence | Accepted events | Inserts and updates | WAL bytes | Collector p95 | Query p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 15 s | 64 | 1538 | 1645660 | 31 ms | 250 ms |
| 10 | 60 s | 16 | 409 | 932781 | 32 ms | 297 ms |
| 50 | 15 s | 288 | 7086 | 5704209 | 31 ms | 625 ms |
| 50 | 60 s | 72 | 2294 | 2421609 | 16 ms | 1047 ms |

At 50 learners the minute run reduced accepted events by 75% and inserts and
updates by about 68%. Query p95 exceeded the 500 ms limit. The sample from
first ACK to the admin answer was 1578 ms p95 on two ticks and includes the
rest of that tick's deliveries. It does not establish end-to-end freshness
for each event. Case IDs are recorded in the local
`s16-release-20261119-023551-cases.json`; all four result JSON files are in
`services/analytics/.local/benchmark-results/`. The four Docker containers
were restored, Web and Core/Collector/Query returned 200, and the F listener
closed.

On separate disposable PostgreSQL databases, 52 Core tests passed, including
the c504 to c528 fixture and dump/restore rehearsal. Forty-six Analytics tests
passed, including a012/a013, a014 tool facts and deletion. The exposed preview
still has the current lesson Tracker, report and personal measurement flags;
tool collection is off. The deployed knowi.ru page and course tracks returned
200. The candidate's completed-count route returned 404 there. The published
manifest still points to the 15 November release; the local working changes
have no equivalent candidate manifest.

The Web release suite passed all 368 main cases and its additional checks.
The build and public-asset scan passed. Test expectations now match the current
block 17 fallback and exercise catalog title precedence. The Core test for an
omitted leader now removes the optional ID in its fixture. Eight local bytecode
directories were moved to a recoverable `.local` archive before repeating the
Core unit run. After those corrections, the Core unit suite had 369 passed
and one failure. The SQLite retirement guard still reports 24 violations. The
Core architecture guard reports 40 forbidden imports. Release helper, backup
guard and Runner tests passed. The local results do not establish production
CI or staging parity for an unpublished candidate.

## 19.11.2026 — delivery and sprint 16 checks

Jira reported 14 of 86 issues done. CORP-117 still requires Core CORP-56.
Core CORP-55 requires completed rollback and notification prerequisites and an
explicit owner decision on the build and cutover window. The Analytics VPS
work in CORP-122 has its own server acceptance and rollout decision.

CORP-428 verified two concurrent claimers against a disposable PostgreSQL Core
database on `c528_course_leaders` with Analytics addon v2. One claimer owned the
row. After expiry, a new token took over with delivery marked uncertain. The
old token could not acknowledge and the new token persisted `delivered` with
two attempts. The temporary database and private operation were cleaned. The
publisher runbook now covers prolonged outages and catch-up. Eighteen focused
publisher and proof tests passed. The scenario uses two database connections.
The separate container rehearsal with the existing Linux publisher image
repaired stale proof and reports, exited cleanly on SIGTERM as PID 1, and
delivered one fact and projection after restart for a change committed while
offline. The HTTP suite exited 0 and confirmed temporary resource cleanup.
[Jira evidence](https://platformpy.atlassian.net/browse/CORP-428?focusedCommentId=10430)

CORP-546's recorded 1/1/1 peak diagnostic on F matched the oracle and missed the
Query p95 threshold, 765 ms against 500 ms. The long peak stopped before a
measurement artifact was saved. No new load started during this pass.
[Jira evidence](https://platformpy.atlassian.net/browse/CORP-546?focusedCommentId=10431)

CORP-551 still needs a genuine hidden transition. Both Batch runs browser
launches hit `spawn EPERM`. A separate headed Chromium ran the loopback fixture
but the document stayed visible across the tab switch and window minimization.
Installed Chrome rejected DevTools control under the administrator policy.
The native Firefox test window became unavailable before opening the fixture.
The isolated HTTP fixture was stopped.
[Jira evidence](https://platformpy.atlassian.net/browse/CORP-551?focusedCommentId=10432)

## 19.11.2026 — raw time across platform tools

The browser now tracks the active learning workspace, lesson list, interpreter,
account tabs, catalog and course pages for signed-in learners. Lesson segments
retain their existing attribution. Tool segments use a stable tool ID and persist
in `analytics.activity_facts` under `browser_tool_v1`. The tools are collected
without producing report work or adding anything to the learner interface.

TypeScript, 49 Web Tracker tests, 64 Analytics tests and 9 Core grant tests
passed. A single disposable PostgreSQL test applied `a014`, retried existing
lesson and new tool facts, and verified one raw tool row with no tool jobs or
study buckets. The test database and roles were cleaned up.

Docker Web, Core and the four Analytics roles were restored and returned HTTP
200 on the page and role readiness checks. The retained preview uses `a011`, with
the existing lesson Tracker active and tool collection disabled. Applying `a014`
and enabling `VITE_ANALYTICS_TOOLS_ENABLED` are separate from restoring the
preview. Load tests remain deferred.

## 19.11.2026 — service implementation first

The owner paused work on reports and the course progress interface. The next
implementation work covers service delivery and durable source facts. Preserve
each accepted lesson interval with its course, lesson, section, start, end and
duration in `analytics.activity_facts`. The existing fact contract remains the
input for future reports and rebuilds.

Begin with the Core publisher work in CORP-428. Its formal acceptance still
follows CORP-117 and the Core migration in CORP-56. Run the broader verification
after the service implementation tasks. The publisher now logs bounded queue
depth for facts and proofs, sampled delivery eligibility and truncation. If
the detail query fails, it falls back to the existing blocked-state check.
Three focused offline tests passed after implementation. The PostgreSQL fault
matrix and capacity acceptance remain pending.

## 18.11.2026 — chapter time on Docker Web

The learner's chapter header now shows the selected total, theory or practice
time for the active Moscow date range. The browser sends the existing complete
course catalog membership in one scoped `POST /api/analytics/reports/activity/chapters`.
Query sums the measured lesson rows from one snapshot before lesson pagination.
Missing coverage is rendered as a dash. The two existing GET routes remain
available with their previous contracts.

The existing a011 Analytics runtime was restarted once with the new route.
The Docker Web container kept serving the same preview. Local API verification
matched all nine chapter totals against the measured lesson rows and passed 304
lesson memberships. In Chromium the closed `Python с нуля` chapter displayed
three measured minutes. Switching theory, practice and total reused the chapter
response and matched its three values. The visible screenshot is stored in
`.local/analytics-browser/analytics-chapter-totals-32c9c79f.png`.

Nine server tests, 32 Web tests and TypeScript passed. No load run was started.
[CORP-93 implementation evidence](https://platformpy.atlassian.net/browse/CORP-93?focusedCommentId=10428)

## 18.11.2026 — implementation and working Docker preview

Work shifted to implementation. The learner's course progress view requests
the selected lesson's report when that lesson is expanded. It uses the active
course, Moscow date range and existing self-scoped grant. The card shows total,
theory and practice with explicit loading, stale, missing and error states.
Requests from the old selection are canceled. The 20-row detail pagination
continues to supply the outline summaries. Chapter totals and browser
acceptance remain part of CORP-93.

Docker Web is available at `http://127.0.0.1:5173/backend/`. Compose reads the
current Web and course source directories through read-only mounts and Vite
watches for changes. Existing Core PostgreSQL, Analytics PostgreSQL and Redis
containers use their retained volumes. Core, the four Analytics roles and the
local privacy authority are restored on their protected Windows loopback
ports. Docker Web proxies their existing routes through `host.docker.internal`.
Chromium rendered the guest workspace with no application or HTTP 5xx errors.
The lesson module loaded from Docker Web. Addressed frontend tests passed 14
cases plus TypeScript, and the local projection suite passed 22 cases.

```powershell
docker compose -f infra/analytics-local/compose.web-dev.yaml up -d --build
docker compose -f infra/analytics-local/compose.web-dev.yaml restart web
```

Fresh personal Analytics instances can select `a011`, `a012`, or `a013`.
Course processing and reads are coupled to `a012` and later. `a013` enables
separate boundary credits. The existing preview continues to use its prepared
dataset at `a011`. There was no schema upgrade for that dataset in this run.

Future load runs will pause only the identified non-benchmark containers,
preserve the list of containers paused for that run, and unpause them on
completion. The comparison on F is deferred while implementation proceeds.

## 18.11.2026 — full matched peak on F

The F-backed test instance was restored and verified before traffic. It ran
PostgreSQL 16.15 on the existing 1 GiB ext4 image with `fsync`,
`synchronous_commit`, and `full_page_writes` enabled. All eight control
commits completed in at most 16 ms. Available Windows commit at the
baseline launch was 21,948 MiB with 5,542 MiB of physical memory available.

The full a011 baseline started at 22:58:32 MSK with 50 learners, 1/1/1
replicas, 15-second collection, and 100 ms Processor polling. Launch case
`5c6bd6f3c6f647c39f138220970af5b2`, wrapper process 15092. Windows logged
power-off at 23:11:51 and booted again at about 23:21:14. The wrapper and
its workers were absent after boot, with no JSON result or exit receipt.
The completed preflight and process uptime provide no latency sample.

The same F image and durable PostgreSQL settings passed preflight after
boot. The short 1/1/1 baseline started at 23:24:07 MSK, launch case
`d6d650748d2b423a9c5d519a606d7301`. It completed 122.063 seconds of
measurement with an oracle pass and Query p95 of 765 ms. The Query threshold
is 500 ms. Run `396bae50-70a7-440f-b59a-cc9417ba90cf`. The short profile used
seed 11320261116, 50 learners, and 60 seconds of warmup. The minute run and
the full comparison are deferred. The dedicated F PostgreSQL was stopped
normally after the phase change.

The Batch runs Chromium v2 fixture stopped when the page closed after a
tab switch and return. The next isolated Chromium run saved its full journal
after a tab switch and return. The fixture stayed `visible` on all snapshots
and recorded no native `visibilitychange`. The 4,290 ms theory segment
matched its observed bounds. The run verified cleanup and leaves the hidden
case open. [Jira result](https://platformpy.atlassian.net/browse/CORP-551?focusedCommentId=10426)

## 18.11.2026 — sprint 16 handoff and peak baseline follow-up

Board 9 lists sprint 16 as active with 14 of 86 issues done. Current branch
`feach/Analitick` is at `f787672ff` with local changes from previous runs.

- The short baseline on F completed with a latency failure. a011 collected
  every 15 seconds for 50 learners with 1/2/1 replicas and 100 ms Processor
  polling. All 288 Collector and 72 Query requests returned 200 in the
  121.812-second measurement. The oracle matched 30 active learners and
  3,600,000 ms. Collector p95 was 31 ms and Query p95 was 563 ms against the
  500 ms threshold. There were 7,273 inserts and updates, 6,288,254 bytes of
  cluster WAL, and database growth from 10,607,639 to 12,147,735 bytes.
  Artifact `s16-resumed-f-smoke-a011-15-stage50-121-poll100-b32b49c466ea4bc1bc8873d47cb8ebed.json`.
  [Jira result](https://platformpy.atlassian.net/browse/CORP-546?focusedCommentId=10424)
- The 120-second baseline and 900-second candidate on F use the same harness,
  profile, seed, oracle, and 1/2/1 replica shape. Their different measurement
  windows prevent a matched comparison. The short a012 on F used a different
  harness revision. The [acceptance profile](minute-activity.md#порог-сравнения)
  calls for matched windows and 1/1/1 at both 10 and 50 learners.
- Windows reported 331–791 MiB of available commit across several probes.
  A lightweight Node process failed with `VirtualAlloc failed`. The benchmark
  now supports an optional commit floor. The local F launcher sets 512 MiB
  and accepts the 1/1/1 replica shape for the next matched pair. The new guard
  test simulates 331 MiB of available commit with 4 GiB of free physical memory
  and verifies that the run records `UNRUN` before provisioning. The selected
  benchmark and reference suites passed 73 tests. Another 20 tests of the
  protective-stop receipt passed after rerunning outside the sandbox that
  blocked the pytest temporary directory. Both dry plans resolved to 50
  learners, 1/1/1 and a 120-second diagnostic on the new harness. Windows
  settings and the page file were left as configured. The new harness
  fingerprint requires both sides of the next matched pair to run again.
- The independent browser chat recorded `spawn EPERM` for headed Chromium.
  Its escalated attempt produced no result before interruption. Native
  browser control stopped at its Firefox URL policy. CORP-551 still needs a
  real hidden → visible transition with matching sent intervals.
- The report review identified the missing a012 browser checks for CORP-559
  and CORP-561. The Redis decision in CORP-562 still requires comparable
  PostgreSQL measurements. CORP-564 and CORP-565 follow those checks.
- Local transfer work has partial evidence. Its formal input CORP-56 is
  still `К выполнению` and requires the separate Core migration and owner
  GO in CORP-55. CORP-122 also requires separate VPS authorization and a
  user-visible rollout decision after local acceptance.

Batch runs evidence is in the Tracker (`01a1efdf-e26f-7612-b012-335906b0b2a2`),
Core transfer (`01a1efdf-fbd0-7551-b127-64a659d21669`), and reports
(`01a1efdf-ec89-77f3-8085-9a2fda86b4f7`) chats. The Coordinator owns Jira
transitions. Next independent work is browser proof for CORP-551 and a
matched peak diagnostic at 1/1/1 after checking available commit and that
the benchmark owns the F-backed PostgreSQL instance.

## 18.11.2026 — stage 50 tuning on disk F

The benchmark PostgreSQL moved to a dedicated ext4 image on Windows disk F
at `127.0.0.1:62333`. The target uses PostgreSQL 16.15 with fsync,
synchronous_commit and full_page_writes on. Eight preflight commits
completed in at most 16 ms at the timer resolution. Owner, sentinel and
runtime role validation apply to the new target. Sixty-one offline checks
passed before launch.

The a012 diagnostic on F used 50 users, 1/2/1 replicas, 60-second collection
and 100 ms Processor polling. The scheduled 120-second measurement took
121.234 seconds. All 72 Collector and 72 Query requests returned 200 and
the 3,600,000 ms final total matched the oracle. Collector p95 was 31 ms and
Query p95 was 453 ms. Acceptance status remains UNRUN for the short
diagnostic. Artifact
`s16-resumed-f-smoke-a012-60-stage50-121-poll100-3fea3facdb3f4b1e94529db179029053.json`.
The results from the previous storage and this run describe different
environments. They cannot isolate the effect of polling.

The full run with the same F configuration completed 300 seconds of warmup
and 901.281 seconds of measurement. All 540 Collector and 540 Query
requests returned 200. Collector p95 was 31 ms and Query p95 was at the
500 ms target. The oracle matched 30 active learners and 27,000,000 ms of
total activity. The measurement recorded 14,957 inserts and updates, 1,106
deletes, 23,646,259 bytes of cluster WAL and a database footprint of
13,646,871 bytes at the end. Available Windows commit headroom reached
about 33 MiB at the lowest sample. This is a single tuned candidate run.
Matched baseline comparisons and release acceptance remain open. Artifact
`s16-resumed-f-full-a012-60-stage50-121-poll100-e75a6ab403fb4053babc2169a0068c42.json`.

The independent Playwright Firefox fixture loaded and activated both tabs.
It exposed no hidden transition. The actual hidden-tab criterion remains open.

## 18.11.2026 — resumed sprint 16 validation

- Applied and reversed a013 against a disposable PostgreSQL database in two
  consecutive cycles. The raw source and measured a012 rows matched across
  both cycles. The conditional history date retained its provenance and had
  no course or lesson attribution. The new round-trip test passed.
- The standard Docker PostgreSQL accepted connections. The baseline stopped
  before traffic while Query was unready. The failed database had complete
  bucket backfill, a draining day backfill and zero facts. PostgreSQL showed
  WAL and data-file synchronous I/O waits. The retry on an isolated Ubuntu
  PostgreSQL cluster backed by disk C also stopped before traffic. The new
  diagnostic identified Query readiness HTTP 503.
- Provisioned a separate synthetic PostgreSQL 16.15 data directory on an ext4
  image backed by the distinct physical disk E. Eight preflight commits took
  15–32 ms, with fsync, synchronous_commit and full_page_writes enabled.
  Matched benchmark artifacts distinguish their PostgreSQL target and record
  table/index footprint and the first-ACK-to-admin-response samples.
- The first run on E reached traffic. It stopped at warmup tick 20 with 168
  Collector 200s, 54 Query 200s and six Query 503s. The Query was ready, its
  sampled cause was `projection_pending_timeout`, and the post-response
  scoped snapshot showed three ready and three dirty buckets. No measurement
  boundary was reached. Normal Processor idle polling now stays at 0.5 s by
  default. Real errors and unavailable dependencies retain exponential
  backoff. The matched warmup now reads actual data from the workload start.
  The 67 focused tests passed before the new run.
- After the polling and warmup fixes, the a011 15-second baseline on 10
  users finished 901.938 seconds of measurement. All 480 intake requests and
  all 180 report requests returned 200. The final totals matched the oracle.
  Collector p95 was 63 ms. Query p95 was 1,390 ms against the 500 ms target,
  so the run failed the latency target. Observed writes were 11,473 inserts
  and updates, cluster WAL was 12,251,727 bytes, and database size ended at
  12,581,911 bytes. Artifact
  `s16-disk-e-fixed-poll-a011-15-stage10-eabda2285830470688774f09bdad2f68.json`.
- The matched a012 60-second candidate finished 901.391 seconds. The
  workload, seed, harness fingerprint, replicas and PostgreSQL instance
  matched the a011 run. Both returned the same 5,400,000 ms total and all
  180 Query responses were 200 in each run. The candidate also failed the
  500 ms Query p95 target. Artifact
  `s16-disk-e-fixed-poll-a012-60-stage10-57a0f001e3b44ee8ab2e6cc99d38aa31.json`.

| Stage 10 measurement | a011, 15 s | a012, 60 s |
| --- | ---: | ---: |
| Accepted measurement events | 480 | 120 |
| PostgreSQL inserts and updates | 11,473 | 3,158 |
| PostgreSQL deletes | 0 | 243 |
| Cluster WAL, bytes | 12,251,727 | 4,878,515 |
| Database size at end, bytes | 12,581,911 | 10,664,983 |
| Collector p95, ms | 63 | 63 |
| Query p95, ms | 1,390 | 1,360 |
| First ACK to admin response p95, ms | 1,938 | 2,093 |

On the verified candidate database, the 16 course-window rows averaged
1,370 bytes per row at a maximum of 1,888 bytes. The eight course-day rows
were 160 bytes each. The two table footprints were 114,688 and 49,152 bytes,
with 16,384 bytes of indexes each. These are stage 10 observations. The
reported sizes and CPU exclude the separately opt-in a013 boundary credit
worker.

The 50-user a011 baseline stopped during warmup at tick 2. Collector accepted
108 requests and three Query requests returned 503. The first diagnostic
recorded `projection_pending_timeout`, with 14 ready, one claimed and 15
dirty scoped buckets after the response. Query readiness was 200. No peak
measurement boundary was reached. Artifact
`s16-disk-e-fixed-poll-a011-15-stage50-69d5f622ab0248e1a001c0faf2dad6b2.json`.

The 50-user a012 candidate with one Processor also stopped during warmup.
The run recorded 72 Collector 200s, 21 Query 200s and two Query 503s. Query
readiness was 200 and the scoped diagnostic showed 18 ready and 12 dirty
buckets after the pending timeout. This was the replay from the same
E-backed PostgreSQL after the PC reboot. Artifact
`s16-disk-e-restored-a012-60-stage50-7fb01d21311a48a9a3e340a28583afc5.json`.

The two-Processor diagnostic at 50 users, a012 and 60-second collection,
finished its shortened 120-second scheduled window. All 72 Collector and
72 Query requests in the window returned 200, and the final 3,600,000 ms
total matched the oracle. Collector p95 was 78 ms and Query p95 was
3,156 ms. The short diagnostic has acceptance status UNRUN. Artifact
`s16-disk-e-smoke-a012-60-stage50-121-928b60e2238440d39de035d0f86805bc.json`.

- In the isolated Playwright context, network emulation changed the browser's
  online state in both directions. Restoring transport retried the identical
  event array and received a synthetic ACK. Actual hidden-tab behavior remains
  unverified in the available browser automation.

## 18.11.2026 — unified sprint 16

- The first 51 reads in each interrupted warmup targeted the empty interval
  before measurement began. At tick 20 the benchmark sent intervals and then
  immediately read the same period. Query now waits for an eligible dirty or
  claimed projection within a bounded budget and rechecks access, deletion,
  dataset and generation after releasing the previous transaction. Invalid
  projections keep returning 503. The benchmark records the first Query 503
  and its available diagnostics without retrying the load request.
- The additive a013 revision persists boundary credit by subject, window and
  course, with derived daily rows. The Processor can fill it behind an opt-in
  flag. Measured theory, practice, active days and streaks keep their current
  sources and API contract. Separate PostgreSQL checks covered both a012
  initialization orders, Query waiting on late partial and full-day data,
  a013 credit across Moscow midnight, invalidation and republication after
  correction, and cascade deletion.
- On a013, the integrated Intake-to-Processor test compared a control subject
  with one whose late event rolled back before commit, then committed with its
  ACK lost. Replaying the same event produced identical measured contributions
  and credits after midnight. An unrelated window and day retained their
  generations. Existing PostgreSQL recovery scenarios also passed on a013:
  cancelled and timed-out intake, repeated Processor commit, expired day
  claim, late bucket fencing and repeated privacy deletion.
- The original local load-test PostgreSQL is rejecting connections during crash
  recovery. The temporary in-memory PostgreSQL was used for correctness checks.
  Ordinary and peak resource measurements and comparable p95 remain unverified.
- Jira sprint 16 active. Recorded the minute collection rules and wire contract in
  [minute-activity.md](minute-activity.md). CORP-547, CORP-550, CORP-548,
  CORP-549, CORP-552 and CORP-554 were accepted individually in Jira.
- Synthetic browser fixture verified two uninterrupted 60,000 ms theory
  segments, a 12,002 ms remainder at a section change, a separate practice
  segment, and correct attribution on the second course. The fixture transport
  has no server. Tracker uses the existing v2 activity segment and preserves
  request identity on retry.
- The current in-app browser run verified four positive partial segments.
  The first course had 17,620 ms of theory and 3,611 ms of practice. The second
  had 20,161 ms of theory and 14,726 ms of practice. Each matched the elapsed
  timestamps. A failed synthetic delivery retried with an identical event
  array and received an ACK after transport was restored. The fixture retained
  `visibility=visible` on switching test views. The real hidden-tab case
  remains open.
- a012 adds derived subject/window/course and subject/day/course rows. The
  original measurements and a011 reader remain available. Isolated PostgreSQL
  verified fresh writes, a011-to-a012 backfill, both query paths, partial edges,
  course authorization, stale-source rejection, deletion and downgrade. During
  the first runtime rehearsal the day writer hit an FK violation before parent
  insert. The trigger now marks the source before and publishes the course rows
  after the parent write, in one transaction. The fresh-write regression test
  passed on a separate target.
- Actual original profile v1 stage 10 (1/1/1) failed in warmup with 84 Collector
  200s, 84 Collector 429s, 51 Query 200s and one Query 503. No measurement p95
  or capacity evidence was produced. The artifact is
  `s16-stage10-baseline-bf2d293220bd44e78f04ba1981d39034.json`. The matched
  cadence mode measures 15 and 60 seconds over identical activity, with separate
  PostgreSQL counter and cluster-WAL boundary samples. The paced 15-second a010
  repeat had 168 Collector 200s and no 429s, but the same Query 503 after 51
  successful warmup reads. The a012 repeat with minute collection received 48
  Collector 200s and stopped on the same Query 503 after 51 successful warmup
  reads. The two runs retained six dirty buckets after shutdown, with completed
  backfills. No measurement boundary or comparable resource delta was reached.
- First a011 browser rehearsal rendered learner and leader, and returned 200
  for consecutive course filters. The all-courses return was 503 and the
  fixture detected a Core visit before the catalog had loaded. A subsequent a012
  rehearsal with all owned services running returned 200 for `backend`,
  `backend_extra`, and the all-courses return. With legacy writes disabled for
  the full cutover lifecycle, the final a012 browser run rendered all three roles.
  The learner's 60-second event committed on the Collector. After a controlled
  lost response, the browser replayed the same event ID and payload and got a
  duplicate ACK. The selected learner made no legacy visit POST in that trace.
- A separate complete a012 synthetic run finished with exit 0. The restore
  uses a fresh dataset run and rebuilds course sidecars from canonical inputs.
  Progress and conditional history parity, deletion-log barrier, signed access,
  guarded outbox replay and return to mirror were checked. Owned database and
  Redis cleanup completed for that run.
- Latest focused suites had 386 passing Python tests and 48 opt-in skips.
  The frontend Tracker/catalog suite passed 46 tests plus the TypeScript check.
  Separate guarded PostgreSQL cases covered actual intake, storage and query.
  Comparable load results and formal Core/VPS release gates remain open.

## 18.11.2026 — working local cutover and ordinary visit retirement

The working platform on `127.0.0.1:5173` now uses Analytics. The persistent
local dataset, Core database, session namespace and privacy journal were
retained. Before installing the additive Core transfer schema v2, a fresh Core
backup was restored and compared on a separate database. All 32 tables and 9
accounts matched.

The sealed date-only export delivered seven historical dates. The source held
zero server lesson and task completion records at the export boundary. Verified
proofs matched the current source revisions for all nine accounts. The two course
catalogs were republished against the same local Collector before browser QA.

On Chromium with the existing QA learner, direct browser collection sent new
theory and practice intervals to Collector. Analytics Query supplied the personal
report. The page showed two measured minutes before retirement and three in the
final check, with an imported date listed separately. The calendar marked 18
November and the streak showed at least two days.

After the first browser check, Core was switched to
`PYMENTOR_ANALYTICS_RETIRE_LEGACY_VISITS=1`. An authenticated request from a tab
opened before the switch received `200`, `X-Analytics-Legacy-Visit: retired`,
and `visited_at: null`. The probe date remained absent. The historical, state and
outbox visit row counts remained 8 each after new browser collection. Authorized
progress capture and the publisher stay active. The ordinary visit route exits
before its writer. The DB write gate stays in `mirror`.

Core's existing booking endpoint returned `active_without_config` both before
and after the switch. The measured calendar now renders independently from
booking readiness. Course leader assignments were preserved.

The local launch scripts are in `.local/analytics-local-cutover-20261118`.
With the local source and existing authority available, run the Analytics
supervisor with `--serve --core-transfer`. The launcher modes are `retired`
for the current Core and `reads` to restore visit recording while keeping
Analytics reads. Switching just between these modes needs no replay because
retirement emitted no legacy visit facts. The publisher runs separately.
The current evidence covers the working local learner flow. Role checks for
leader and platform administrator were performed on the earlier isolated stand.
Jira evidence is recorded in CORP-431 (10369), CORP-429 (10370), CORP-92
(10371), CORP-88 (10372) and CORP-105 (10373).

## 18.11.2026 — course completion summaries and learner activity reads

Verified on isolated Core and Analytics PostgreSQL databases in `feach/Analitick`.
With Analytics legacy-read mode and `PYMENTOR_ANALYTICS_ADMIN_COMPLETIONS_ENABLED=1`,
Analytics computes the course completion count, daily trend and seven-day ranking
from delivered lesson facts. Core supplies the authorized roster and display labels.
The leader's completion roster is scoped to their bookings in the selected month.
The platform administrator can select a course from the existing overview.

`PYMENTOR_ANALYTICS_LEARNER_READS_ENABLED=1` exposes the authenticated history
and measured calendar reads for learners. The personal statistics view shows
revision-zero imports as conditional dates with 1 h theory and 1 h practice per
date. Measured totals, calendar marks and the streak use positive measured time.
The streak is a lower bound when completeness is unknown. Core continues to
serve the booking schedule and quota.

Chromium verified the learner, leader and administrator with three synthetic
sessions against the built web application. The learner saw four conditional
dates and 1 measured minute even though 18 November occurred in both sources.
The activity calendar marked 18 November and omitted history-only 16 November.
The streak showed at least one day. Both staff views showed one completion on
`backend` and the leader received HTTP 403 on `backend_extra`.

`tools/test_cross_service.py --run --chain --a010 --a011 --progress-history
--admin-completions --learner-course-review --hold-seconds 300` exited 0 with the
synthetic-run opt-in enabled. The run proved Core SQL bypass for completion reads,
course and learner access, Query outage handling, guarded write-mode rollback,
deletion and the restore barrier. The successful run cleaned its owned databases.
Jira evidence is in CORP-89 (10364), CORP-92 (10366), CORP-88 (10367), CORP-105
(10365) and CORP-431 (10368). The isolated browser used previously accepted
synthetic measurements. Production rollout, full CORP-105 acceptance and
retirement of ordinary Core visits remain pending.

## 17.11.2026 — progress, date-only history and global completion reads

Implemented and verified on owned disposable Core and Analytics PostgreSQL
databases in `feach/Analitick` (base `f787672ff`). Three opt-in settings:

- `PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED=1` writes confirmed task changes and
  their outbox facts in one Core transaction. A repeat of the same task revision
  has no second effect. The existing `tasks` export, seal and subject proof v2
  cover pre-existing tasks and the newer live version. Enable capture only after
  deploying task-aware receiver and processor. A read rollback retains this
  compatible data path and does not downgrade consumers to pre-task code.
- `PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY=1` exports dates without old timestamps
  for new historical batches. The existing nullable field remains in the wire
  shape. Dates-only mode is held fixed during the export and proof staging. The
  standalone signed self-history route returns 1 h theory and 1 h practice per
  imported day with `core_legacy_conditional`; it contributes no measured facts.
  A legacy-calendar response in this mode intentionally loses the old timestamp
  on a backfilled date. The compatibility export with the option off retains it.
- `PYMENTOR_ANALYTICS_ADMIN_COMPLETIONS_ENABLED=1` with Analytics read mode
  reads global dashboard lesson aggregates from Analytics on a verified roster
  of at most 80 subjects. Core supplies display labels and applies the previous
  tie order. Both global endpoints keep their existing response shapes. Course
  dashboards and measured active-day/series semantics remain separate work.

Verification: `tests/analytics_delivery` 140 PASS; Analytics
`tests/core_transfer` 46 PASS, 2 environment-dependent skips with isolated
PostgreSQL enabled. `tools/test_cross_service.py --run --chain --a010 --a011
--progress-history --admin-completions` exit 0. Observed Core backfill/live task
revisions, repeat without extra fact, verified proof v2, authenticated history
with four tagged days, and both global overview responses matching the Core
baseline while a test guard prohibited reads of `user_lesson_progress` in Core.
The same run exercised guarded reading, replay to mirror, deletion, restore
barrier and stale lifecycle rejection. The two earlier rehearsals exposed legacy
timestamp differences in expected responses; both were reconciled and their
owned databases, roles and Redis fixture prefixes cleaned along with the final
successful run.

No protected runtime flags were enabled. The Core readiness prerequisite
CORP-56 and release acceptance remain open. The UI for conditional history,
course-scoped completion offload, measured activity/streak migration and final
shutdown of ordinary visits still require their own verification.

## Latest: local Personal Analytics connected, 17.11.2026 16:31 UTC

User-reported empty Personal time traced to OFF frontend flags/Core proxy and
synthetic-only backend configuration. New protected local instance
1742f31e-be55-40fe-8e76-d0d98adf2d65 is running independently of retained test DBs.
Actual learner browser B2 ACK ->14facts->ready bucket/day->Query200->My data PASS:
109803ms total=45612theory+64191practice; UI1min, progress9/280; full reload PASS.
Both backfills complete/run matches. Backend and backend_copy catalog accepted
after fixing bounded Unicode legacy aliases without relaxing canonical IDs.
WebTracker/Reports/Personal ON, cutover OFF; Core legacy/mirror retained.
Checks and limits: `local-personal-live.md`, `local-personal-runtime-runbook.md`;
Jira92/comment10355. Local-mode full delete/outage/restore matrix remains open,
coverage unknown, staff own learning time excluded. Main runtime left available;
this is not complete Core offload, K3s load or production acceptance.

## Current checkpoint: a011 integrated; K3s first load not started

Latest follow-up17.11,14:13UTC: first K3s case b551ed25-a61a-4b3a-a77a-
4dadf1dcb11a failed/UNRUN BEFORE warmup. Namespace UIDab284d5f-0d49-4abb-
b2d3-1ce80787dd9b; DB analytics_test_58a23e47212c4ae79fde0a0783d2549f;
binder UID312c0df8-96cf-46a7-a31d-9691c0eb3017;
PVC62f2941c-e631-46c3-bd5c-18710039b47e/PV8f56cd3b-8856-4c5e-aef4-fc760893c150.
Image probes/namespace/DB/migration/projection begin/Secret/PVC/binder confirmed;
init Job/authority deployment/Helm/load never started. All pending operations
have confirmed records. Runtime shapes were stricter than harness assumptions:
containerd reports canonical manifest imageID + config image; local-path uses
PV spec.local.path. Harness now accepts only those exact attested shapes with
strict digest mapping, UID-derived path, selected-node/nodeAffinity;206offline
tests+53subtests PASS. Read-only actual image/binder/storage recheck PASS.

Host memory then fell below1536MiB. Binder cleanup did not reach proxy/delete;
all resources and benchmark-k3s.active.json still retained. Protective exact
node stop operation d11af186-fed2-4947-b71d-6fc7d8d9a703 confirmed STOPPED
14:06:51.140275426UTC, exit137/OOMKilled=false after15s stop timeout. This was
not graceful shutdown; data/objects retained. Intent and separate confirmed
record are .local/node-protective-stop-d11af186-fed2-4947-b71d-6fc7d8d9a703*.json.
No second benchmark attempt. Old4 deployments remain0; retained authority OFF.
Main Core18041/frontend5173 HTTP200. Latest free2150.3MiB; full strict K3s budget
is not available. Never lower floors to continue. Next: new budget/node start,
UID-precondition binder cleanup/reconcile original marker, then NEW case UUID.

Continuation guard prepared: default stopped exit143 unchanged; explicit
protective_stop_receipt=run_k3s.PROTECTIVE_STOP_CONFIRMED admits only known
d11af186… paired records/exact137/nonOOM/FinishedAt/retained mounts.226offline
+53subtests PASS; final receipt file-hardening focused20PASS. Root actual
Docker-inspect+receipt validation PASS. Full restart budget then REFUSED with
hostfree2150.3MiB; main18041/5173HTTP200. No start/delete/new workload. Jira107/
comment10354. This real stopped-proof check does not prove a future restart.

Legacy smoke6e7c1384-9ca8-407c-b52e-355b9db2fcd6 now PASS: frozen build
4107334d… on fresh copy, real authenticated HTTP/calendar/summary/admin/progress
and negative ACL. Root independently checked safe stdout and all3original
SHA95c70d6a…/425984B/mtime/no sidecars.3owned containers/image/private dirs
cleaned with independent absence checks.48audit receipts kept under
.local/analytics-legacy-audit/916e3969fed649d7ba44de938c74a6dc;
manifestSHAe7e91f132837382d4fa93fe63baf8e87d3b1cc6076394936e512ae74a47049c5.
Jira108/comment10349; legacy-preservation.md. Prior FAIL evidence retained.

New cutover correctness fix is SOURCE/OFFLINE only so far: Core server-owned
PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED default0;1 requires analytics read
and verified browser metadata, otherwise503/unavailable. Existing published
catalog response advertises exact required-policy header, and Web enables
Tracker cutover only with that header+valid catalog. Until then learner uses
legacy fallback with POST{} (server Moscow date); staff collect neither source
under requested cutover. Old local-date baseline and lifecycle cancellation
preserved. Root41Node tests PASS; Core49distinct PASS (48initial+1fixture-only
retry). Earlier wrong-CWD failures and missing catalog_version in old synthetic
fixture corrected without relaxing guards. New actual PG/B2/browser/fulltsc
are UNRUN. Cross runner now sets required1 only with a010 and analytics reads.
Both main/frontend flags remainOFF; no real dataset cutover.

Batch course-admin read-only review found no new frontend defect. Platform
Batch review identified same-filter refresh and midnight-stale-today defects;
bounded source fixes completed. Root16focusedNode PASS: same-filter apply
starts a new request/cancels previous/ignores late response; Moscow midnight
and visible-tab return refresh only overview, mismatched response day shows
unavailable. No polling/UI redesign. Browser/live/fulltsc UNRUN for these later
changes. Jira105/comment10353; Core gate431/comment10351; remaining derived
completion aggregation on Core documented89/comment10352.

### Checkpoint before these follow-ups

17.11.2026, latest: public encrypted a011 restore/unseal CLI and real Core
calendar/admin/progress/proof checks PASS. Confirmed source loss to first
guarded reader 6.406s, to positive browser+Core reads 6.860s; one controlled
post-snapshot fact was lost as expected (4→5→4). This is one small synthetic
recovery, not an RPO/RTO guarantee or the 900s backup schedule. Independent
owned-resource cleanup PASS. Jira108/comment10345; day-recovery.md.

Actual PG negative membership test PASS (1test/1.03s): collector direct read
denied, SET ROLE through NOINHERIT membership could read, pristine restore
rejected that target before writes. Owned DB/6roles cleaned.

Clean image694d6065…/manifest1fc54f2a…/configb1a7d720… passed full packaged
probe84708fb8… and was imported once into exact v2 node96cc40b0…;
containerd manifest/config/layer/source attestation PASS. Node is RUNNING,
2GiB memory cap, no container CPU quota; inherited Docker VM NCPU=1. Retained
authority d314… remains STOPPED. Existing four Helm-owned deployments
(collector/processor/query/privacy) are scaled0 with UID/resourceVersion
preconditions; rollback snapshot and separate operation records retained.
No benchmark namespace, DB, PVC, new Pods or load have been created yet.

One reconciled Analytics PG restart preserved config, volume, DB catalog and
both retained a006 schema/sentinel/fact fingerprints. Old max4779/oom0/kill0/
swap36864B were historical; fresh lifecycle counters max/oom/kill/swap0.
After old deployments scaled0 strict budget PASS: available4471406592 versus
required3669200896 bytes. Main Core18041/frontend5173 remained HTTP200.
First node start was protectively stopped for insufficient post-start budget;
only the later start passed. Thresholds were not relaxed.

Legacy preservation runtime107c2712…: frozen image build PASS52.75s and
container create PASS; runtime exited1, OOMKilled=false. HTTP/data/ACL smoke
has NOT passed. Original SQLite plus both archived copies retain the expected
SHA/size/mtime and have no sidecars. Owned copy/image/stopped container and
private diagnostics remain for one bounded phase-diagnostic run. No retry of
the full build/run until the cause is established. See legacy-preservation.md.

First K3s case is being prepared (stage10, replicas1/1/1, repeat1,
300s warmup+900s measurement). Full 60-case profile needs at least20h;
neither source/unit checks nor the dense local sample close CORP107/119.
Core56 and production454/122 remain separate gates.

### Previous checkpoints

17.11.2026: a011 integrated PG suite68PASS/1denseSKIP, including7 actual PG
cases; first query-only dense20correct, p50=328ms/p95=406ms, EXPLAIN259.403ms,
temp0/0. New disposable DB/6roles cleaned. This reaches the500ms local sample
target but is NOT measured worker/backfill/ingestion/privacy-network/K3s SLO.
Jira84/comment10339 and107/comment10340. a011 history8a9e80bd33828e53d737fe1f5803af9bb41dd72f95b073fbffb402b90720aa50;
a010/a006 prefixes unchanged. All default day flags OFF; no retained migration.

Reporting-OFF completed with one browser sequentially, fixture02e86… released
exit0/IDreusePASS/independent3DB12rolesfixtureRediscleanup; Jira105/comment10338.
Main18041 PID10672 and5173 PID25120 healthy, four main frontend flagsfalse.

All9 real PG race cases nowPASS:8initial+1 after test-only timestamp CAST fix,
every DB/6roles cleaned; product/migration unchanged. Jira84/comment10341.
Actual dense BUILD exit0/145.32s:960dirty/0payload→960ready/960payload in89s,
961cycles/0failures/backfillfailures;20correct reads328/406ms,EXPLAIN317.785,
temp0/0. log.local/a011-dense-build-20261117-739f4af4ef7946309970f477a502a3b6.log;
target2d1ce604…/6roles cleaned. Processor real, source readybucket and privacy
stub; not fullingress/load/production. Jira107/comment10342. See perf doc.

Native a011 fullchain now exit0/session88083: signed late B2/day rebuild,
guarded logical restore/newRUN/privacy purge/Core IDreuse; independent3DB12roles
and Redis cleanup. Jira105/comment10343. See local-a011-release.md for exact
source/artifact checks and DB names. Operation UUID unavailable after cleanup.
Linux a011 artifact source/import/defaultOFF/pipcheckPASS, Jira119/comment10344,
but full packaged probe rejected cached bytecode. Initial index6e12085e… must
NOT be imported to node. Confirmed Docker29 archive76,405,248B/config2ef519…
is retained only as rejected-package evidence. Preparation83a7bac5… pending;
independent diagnostic dfc42dd8… exit1/cached bytecode, exact container absent.
Root fixed only Analytics .dockerignore recursive cache exclusions; runtime
and migration SHA unchanged. Clean replacement build after recovery test.

Clean replacement image694d6065…/manifest1fc54f2a…/configb1a7d720… now full
packaged PASS84708fb8…/zero-cache, archive75,885,568B/SHA89390505… preserved at
.local/cross-service/c11cd07a62d64bf693fabb8a16713d2e. Active marker/probe absent.
Previous rejected archive/pending retained only for audit, not node use.

Encrypted a011 helper source-down rerun PASS1test/14.41s total(notRTO), actual
source container stopped/crypto parity/newjournal/purge/newrun/both workers/
reconcile/role-switched reader/final privategrant/60000ms survivor. Independent
cleanup2containers/networks/artifact/key/journal/stage; ready snapshot, not
in-flight claims or HTTP. Current E run tests PUBLIC restore/unseal CLI wiring,
49offlinePASS first; no concurrent Docker build/PG/load during that RTO window.

K3s v2 still STOPPED. Inventory found current node CPU uncapped (do not claim
1CPU), memory2GiB. Retained authority d314… stopped. Restart helper rejects even
that stopped authority; worker adding explicit exact stopped-identity mode with
unchanged default running-authority rules. PG guard sees historical max4779,
oom0/kill0, swap36864B, no current pressure. After E cleanup root will check zero
Analytics clients and restart ONLY exact Analytics PG with same volume/config
for a documented fresh lifecycle; strict max/swap/pressure criteria unchanged.
Cross worker prepared live GRANT/SET ROLE negative case, subsequently PASS above.
Root integrates and runs exclusive measurements. No overlapping PG/load/build.
Encrypted a011 restore was initially fail-closed; public restore/unseal is now
implemented and passed the isolated scenario above. Old privacy retirement before any a011 rows is
mandatory even with flagsOFF. Core56/production454/122 remain external gates.

### Earlier checkpoint, 17.11.2026 after 10:29 UTC

Latest browser hold2da86bcd7fff4f5fb44ccce2dcadde12 expired normally: full-chain
exit0/post-hold ID-reusePASS, independent3DB/12roles/privatefolder/Redis cleanup.
ReviewNode session36666 exit0 with owned cache cleanup; root/course/platform
Playwright sessions closed. Main5173→18041 remains separate/default flagsOFF.
See browser-check-20261117.md for exact factual UI numbers, idle/blur/refresh/
logout and boundaries. Reporting-OFF was completed later as above; true
hidden/sleep remains UNRUN.

Account GET-only503 retry now250/500/1000/2000/4000/8000ms/max7, identity/scope
and event-supersession cancellation.12fake-clock tests and full projecttscPASS;
actual browser2.5s mock→real200 verified. No auth or business-write retry.

Query performance remains a blocker: baseline p95=3110ms, v1=1625ms,
v3=1453ms, target500ms NOT reached. V3 correctness5PGtests+20dense readsPASS,
all owned targets cleaned. V3 source21bc5d973e1901d759085b1613b5f580e5c8e99fb2448b08495ee6ac1a6ed9b7.
V4 regressed p95 to3125ms and was discarded; exact v3 restored. Separate v3
phase profile wall p50/p95=1437/2078ms, snapshot=1313/1969ms; independent
quantiles are not additive. Both owned datasets/roles independently cleaned.
See dense-month-20261117.md. No measured workload is currently running.

Common subject×day a011 implementation started after lock/compatibility review:
day-projection-spec.md. Terra ownership: analytics_day_store migration/store/pure
normalizer; publisher_fix_after_reboot Query; analytics_tracker_debug privacy;
analytics_day_runtime config/factory/runtime; root integrates and verifies.
Default flags OFF; retained a006/main datasets untouched. Retirement of old
privacy workers is mandatory before any a011 personal rows, including triggers.
Root integrated offline85PASS/7PGskipped, then actual fresh-PG day suite
68PASS/1dense-skipped, including7 real PG cases. Sentinel fixture cleanup
assertions passed for every owned DB/6roles.500ms and full integration not yet
proven. Readiness P1 (processor cannot EXECUTE query-only a010 lock function)
fixed with role-specific SELECT; day-only HTTP health now includes day probe,
co-located bucket draining stays independent. Backfill failures observable
with aggregate counter/rate-limited warning. New actual PG factory test passed.
Jira: browser evidence CORP105/comment10335, dense results CORP107/comment10336,
day design CORP84/comment10337.

Reporting-OFF hold b17b1abe906145b98e789c282e68fa32 stopped at RAM<1536MiB
with three concurrent browser sessions; no complete OFF PASS. Sessions closed;
independent cleanup confirmed3DB/12roles/Redis absent, private diagnostics kept.
Subsequent single-session hold02e86e0b04ba46be8ad8a69b2caf7d66 passed reporting
OFF desktop checks for learner/leader/admin; actual old API200 and no new report
requests. Both DBs a010, collection/cutoverON. Release exit0/IDreusePASS; separate
3DB/12roles/fixture/Redis cleanup confirmed. See browser-check-20261117.md.

First a011 dense query-only run, exec15456, completed exit0:
log services/analytics/.local/a011-dense-20261117-a97665f9a12c48ea9f385580bf969296.log.
No simultaneous browser/K3s/build/load. Synthetic seeded ready days do not
measure worker/backfill or production SLO; result328/406ms is recorded above.

K3s runner source now has digest-pinned node images/pre-execution manifest→
config attestation, separate immutable Docker host IDs without RepoDigests,
bounded docker-save config-only parser and source probe; owner writes fsync.
analytics_k3s_load_wiring reports185offline+53subtests, actualDocker29archive/
nodeimport/K3s/PVC/CNI/load stillUNRUN. No registry/push used or authorized.
Before runtime: freeze final Analytics source, build/source-attest new image,
read actual stoppedv2/node/storage identities, resource preflight, narrowGO.
Core56/production454/122 external gates remain open.

### Continuation after limits reset, 17.11.2026

Main API18041 and frontend5173 restored, all three health/page/proxied tracks
checks HTTP200. API is foreground exec session35415; VitePID25120 targets18041.
Four main Analytics flags stayfalse; retained schemas/data untouched.

Autonomous publisher real PG/HTTP PASS, including stale cut1→3 repair,
Windows forced-stop/offline mutation/restart/current cut4/exactly one fact and
projection, self/profile/global200. Independent cleanup3DB/12roles/Redis
namespaceempty. Jira10330. Canonical stage_subject serializes pending cuts
under gate→user for publisher and manual staging; quarantine remains strict.

New local Linux publisher target built: Docker imageID
`f307128472b60789b400e6f25724a6eb65fc0f4ea3c336263296ef956c9dec02`,
OCI config`baa94d9ece084e4f6406f5166b45dba9b08342d8cf53858b24cc6e1e9b09743c`.
Nonroot10001, healthcheckNONE, defaultOFF rejection beforeDB/network,
Python3.12.16/moduleSHA3494ac3c88f0bb7da7a2d4f3f4292052cd73fff1f0e266c4290815896bc22100,
operator tools absent. Current source109AnalyticsDelivery checks PASS in
bounded network-none readonly Linux test image. Linux real SIGTERM UNRUN;
cross_after_second_reboot owns opt-in Docker publisher harness and is preparing
loopback TCP relay to preserve product plaintext-loopback URL guards.

Dense month Query-only: 89280buckets/6428160presence intervals/20reads,
correctness1PASS/87.09s, exact freshDB/roles cleanup verified. p50=2078ms,
p95=3110ms exceeds500ms target; EXPLAIN1944.188ms/temp17463read18304written.
Jira10331. publisher_fix_after_reboot now owns Query SQL optimization and the
exclusive focusedPG+dense rerun window; cross functionalGO temporarily held.
Root does not run competing builds/browser during these measurements.

analytics_k3s_load_wiring resumed Terra after usage limit reset, owns safe
fresh namespace chart guard, optional UID-bound cgroup fixture and new runner.
Offline helpers exist; CLI orchestration still INCOMPLETE, K3s/loadUNRUN.
Do not claim source helpers or native tests satisfy60K3s workload windows.

Root browser fallback: CUA transport closed after idle transport RAM cleanup;
use installed PlaywrightCLI0.1.24, isolated bundledChromium with no user profile
or Chrome policy changes. Prepare private config before next fresh hold.
Actual Workspace capture remains UNVERIFIED: collectgrant200 after fetch fix,
but reconciled interrupted student7 data had0facts/receipts. Tracker24offline
checks PASS; temporary app DOM probes removed. Jira10329.

### Resume after PC reboot, 17.11.2026

Latest continuation: a second PC reboot is confirmed at 11:05:23 Moscow
(08:05:23 UTC). At 08:10 UTC no local listeners or Docker Linux pipe exist;
4696 MiB available. Core runtime agent owns engine/protected Core PG/Redis
and main API/Web restoration; cross agent owns exact Analytics PG and orphan
synthetic fixture reconciliation. Do not reuse a dead holder PID or assume
that the old hour-long hold is live. No retained schema changes.

Before that reboot: encrypted restore passed (Jira10327), new real
profile/quota/outage/lifecycle-race full chain passed with cleanup (10328).
Both Batch Runs performed real staff UI checks; course mobile overflow fixed.
Student transport failure isolated in real browser: native fetch was called
with BrowserTransport as receiver, producing Illegal invocation. Both calls
now use a local function reference; real collect-grant HTTP200 observed.
B2 persistence, theory/practice, real idle and personal UI remain UNVERIFIED.
Jira10329 records the exact scope. No claim of full browser acceptance.

Autonomous Core publisher is implemented behind default-off flags with
20 focused offline checks. Runtime/packaging and independent review proceed;
real PostgreSQL process/restart proof must follow reconciliation. Manual
snapshot staging in earlier fixtures does not prove autonomous freshness.

The following paragraphs describe the earlier recovery and are historical.

Latest state supersedes pre-reboot resource/restore blockers below.
Docker29.9.2 restored; protected Core PG/Redis and Analytics PG started with
existing volumes/ports, no retained DDL. Main Core18041 (PID16060) and Vite5173
(PID17512) initially restored/HTTP200, then deliberately paused for test memory;
root MUST restore both before final handoff. DBs remain running.

Expanded encrypted a010 source-down proof PASSED: Core days/progress/proof,
browser data, quarantine/current journal HWM1/new UUID/rebuild/guarded reader;
local loss-to-reader6.594s, rebuild0.188s, one test12.68s, not productionSLO.
Migrate SELECT on fresh bootstrap sentinel fixed and negative ACL verified.
56 offline checks passed; exact temporary containers/networks cleaned.
See local-a010-release.md for actual IDs/fingerprints and preserved prior failures.

After authorized RAM cleanup (Telegram, own idleSerena, DockerGUI; normal
Yandex main-window process termination without /F), elevated available4173MiB.
Native Terra `/root/analytics_cross_resume` now owns fresh no-hold full-chain
profile/quota/race window. Its previous two refusals created NO resources.
Then distinct900s hold + three-host UI review is planned, not yet executed.
Root IAB2 selected, no temporary tabs yet; Firefox is untouched.

Native `/root/analytics_recovery_resume` completed; `/root/core_runtime_resume`
completed initial host restore. Existing Batch Runs remain UI owners:
course `01a1e154-c842-7a61-b6ce-dcb9512c1980`, platform
`01a1e155-1789-71d1-9523-f91e842326eb`, host `local`.
Root owns Jira/runtime/integration. Formal Core56/454/122 remain open.

### Prior checkpoint before reboot

Resume from [LOCAL_ACCEPTANCE_20261117.md](LOCAL_ACCEPTANCE_20261117.md),
not the historical entries below. Branch `feach/Analitick`, unchanged HEAD
`f787672ff2639434f0be331d05def5d8f31b3c1e`, shared dirty tree; no commits/push.

- CORP-62/65/114/115/116/109 and all23 stage1 prerequisites accepted in Jira.
  K3s v2 server and authority deliberately paused after acceptance; keep data.
- Canonical a007–a010 full-chain passed exit0, exact fresh DB/role/listener
  cleanup verified (Jira10319). Retained Core DB and both a006 datasets untouched.
- Sparse31d/50subjects/12400facts passed; single reader234ms, not load p95.
- Current local images and network-none Python3.12.16 smoke are recorded in
  LOCAL_ACCEPTANCE; stage1 frozen image is NOT the latest a010 image.
- New Core admin profile/quota offload source passed112 tests. Its added live
  SQL detector/outage/reused-ID race is prepared and UNRUN after source fix.
- Personal Dashboard and both existing Batch Runs UI source complete,
  feature flags off by default, TS/focused tests pass. Real3-role browser UNRUN.
- Encrypted a010 source-down restore failed during extraction, before data
  import/unseal. Empty owned source/target containers/networks reconciled and
  removed; encrypted artifact/key IDs/current authority journal retained.
  Baseline app CONNECT existed in failed fresh provisioning; source now revokes
  before extraction (focused checks only). No-network seekable extraction proof
  passed. Paired host Popen reproduced missing output destination; helper now
  passes `--file=-`. Core days/progress/proof fixture integrated;41 offline
  checks passed. Expanded live retry refused before any provisioning because
  RAM fell below prestart floor; corrected restore/unseal still UNRUN.
- RAM fluctuates near prestart2048/runtime1536MiB limits. No dense/load/hold
  without fresh preflight. Root CUA stopped at unsupported Windows Firefox URL
  enforcement; Firefox was NOT closed, no policy bypass. Main Core18041/Web5173
  and proxied tracks last checked200. Main Vite was briefly paused and restored
  as PID31064 with4AnalyticsflagsOFF; Firefox was not closed. All native and
  Batch Runs current source tasks terminal; no background runtime/load is active.
- Core56/454/122 formal production gates remain open; no VPS/DNS/deployment.
  Full profile-v1 requires at least20h of measurement windows; not yet run.
  Linux/Python3.12.16 Core test image:95 focused checks passed, networknone.

Root owns Jira/runtime/integration; native Terra agents and existing two Batch
Runs IDs/ownership are recorded in LOCAL_ACCEPTANCE and BATCH_* reports. No need
to create replacement chats or replay accepted stage1 work. Heavy test windows
remain sequential; source work was parallel. See local-a010-release.md for
recovery artifact identities and any latest diagnosis/cleanup.

## Historical checkpoint before stage1 acceptance (superseded)

17.11.2026, user explicitly requested completion of all Analytics sprints with
parallel Terra and existing Batch Runs. This authorizes local implementation;
production cutover, DNS/VPS and formal Core56 gate are not bypassed. Do not end
this execution merely because individual workers return preparatory PARTIAL.

- Root fresh RAM8609MiB preDocker/5942MiB after; Docker29.9.2 started. New stage1
  image `knowi-analytics:an1-20261117-metrics`: index3bbed65491fdbce339c9231cdd55d23617339a99a91a7d6b376afde547bf0cef,
  platforma93c49f13133af5655360fab18e13f118458a546ca259d1ced4f99709ede3892,
  configb8f3395a83c5acca0586f894077b1c3ff2aec5010a08b1cfbd2d2da0d23f7a53.
  At build root verified exact source/a006 history and4role UID10001/live200/
  ready503/private metrics/RSS/graceful. Host future-source has changed since;
  this remains an immutable stage1 snapshot, not the current future feature build.
- `analytics_infra_finish` (Terra xhigh) owns infrastructure62→114/115/116.
  NEW cluster `knowi-analytics-01a1df75-v2`, context suffixv2, private separate
  kubeconfig, same loopbackAPI57443. Old cluster/node/token/config are preserved
  stopped and quarantined, never reused/deleted. Fresh v2 secret not printed.
  New node96cc40b0343d9387bed6f1721895095b211eaf897276b5c62e247c728fadeb4f.
- v2 actual: API/node/systemPods Ready; own PG restarted preserving volume;
  new separate K3s DB; direct import OCI index→manifest→config verified;
  one-shot Job a006 Complete, independent helper state ready. External private
  authority verified after narrow Windows/Desktop bind-path matching fix.
  Namespace/trust/catalog/Secrets/EndpointSlice installed without secret output.
- First collector-first Helm release could not open without privacy cursor;
  failed stateless release safely removed (facts/jobs/cursor all0), privacy-first
  thenC/P/Q installs succeeded. Signed fresh HWM0 and matching appliedcursor0.
  Root independently observed4Pods Ready/0restarts/imageconfigb8f339 and migration
  Succeeded. CNI positive DB/authority + negative Processor→Collector and
  Collector→K8s API measured; public ingress/metrics denial still UNRUN.
- Guard hit85% raw due reclaimable file cache after imports. Root allowed two
  separately measured target-local cgroup reclaims256M then512M, swappiness0,
  unchanged caps/reserves; no global cache/WSL changes, swap/pressure/OOM stayed0.
  Independent1→2→1 verified for Collector/Processor/Query/Privacy, others1.
  Next62 evidence: actual signed B2 ACK/control projection then serial restart
  every role and verify preserved data/receipts, before root accepts62 in Jira.
  Latest progress comment62=10306.65 remains Done10302.
- `core_analytics_transfer` (Terra xhigh) owns Core default-off capture/outbox/
  lifecycle/grant API, HTTP transports/tools/tests. Newgrant paths
  POST /api/analytics/grants/collect and /report; response grant/actor subject_ref/
  expires_at/server_time; user own reports, leader bounded fullpersonalroster,
  admin global. Core venvPython3.14.6 PyJWT2.15.1/cryptography50.0.2 installed,
  26focused tests; original118 OpenAPI paths unchanged. Followup still active:
  strict total HTTP deadline with slow-header/body tests and optional user_id
  mapping→target_subject_ref for UI drilldown. No PG/DDL on retaineddata.
- `analytics_core_receiver` (Terra xhigh) owns transfer_v1 DTO/router/service/
  PGstorage, futurea007 SQLspec in plans (NOT active migrations), and now mixed
  source replay/quality/processor/privacy compatibility + bounded runtime/checker.
  Common raw ledger source_kind defaults browser_v2; legacy Core fields nullable
  only in future expand, no invented course/minutes. Two shared internal
  projection/entity tables; mandatory purge/counts/grants. Normal intake rejects
  subject_deleted; deletion goes independent signed journal and checked purge
  endpoint. a007 MUST NOT be activated until old stage1 validation complete and
  a006 processor/query/replay retired before Core intake. Core receipt contract
  accepted/absent/conflict means storedraw+job, not computed. Wire fingerprint
  and deterministic UUID aligned with Core. PG tests prepared, not yet run.
- `analytics_activity_reports` completed isolated DTO/pureglobalunion/multisubject
  bounded PG reader + signed-grant router. Root wired Query factory/fence and
  shared HTTP errors (preserving Collector compatibility). Root36focused tests
  passed; worker32passed/1PGskip. Limits31days/100subjects/10000facts. Successful
  observed snapshot ok+qualityunknown; never complete/no_data/freshwatermark
  without evidence. Needs realPG/signedprivacy/factory tests and source_kind
  browser-only filter once a007. No materialized time projection yet.
- `analytics_tracker` (Terra xhigh) now owns new browser tracker/transport/server
  clock/React hook in features/analytics-tracker. Memory-only200events/512KiB/24h,
  idle300, strictB2, course-specific batches/grants, identity/focus/sleep boundaries.
  17unit tests +tsc/B2 validation; real integration/browser pending. Root must
  wire Workspace callbacks, catalogVersion, explicit VITE enable only after grant
  clock validation; no real user collection enabled. Short undetectable sleep
  remains browser limitation, serverunion mandatory.
- User-owned existing BatchRuns reused with authorized messages/ Terra xhigh:
  course thread01a1e154-c842-7a61-b6ce-dcb9512c1980 (course-specific admin files),
  platform thread01a1e155-1789-71d1-9523-f91e842326eb (PlatformAdmin/overview).
  Both purecomponents and coverage tests pass. Latest followup asks actual page
  integration + grant/report loading, preserving previous UI/legacy fields.
  Platform thread exclusively builds shared features/analytics-reporting/client.ts;
  course consumes readonly. Root owns sharedVite proxy/auth/Workspace integration.
  Wait cursors course9298f2dc-e7f2-4161-ab2b-d644790062e4:1,
  platform32e22c4e-853c-4e4e-96af-1c13998e049a:1 (new followups after these).
- RootJira remains owner. Source edits are preparatory until actual dependency
  acceptance, not blanket Done. Core56 still To do; stage2/3 local implementation
  proceeds by latest owner scope but formal gate/production remain separate.
  Core/API/front may be off after user cleanup; restore local platform before
  final handoff. No commit/push/production/real-data backfill or destructive reset.

## Previous checkpoint: CORP-62 retry remains blocked before allocation

17.11.2026, repeated user request to recheck memory and continue. Terra xhigh
`analytics62_resume` completed a bounded followup; root verified the result.

- Added a read-only stopped-node restart admission in `tools/run_k3s.py`.
  It attests exact stopped node/binary/kubeconfig/PG/network/volume identities
  and reserves full stopped-node cap plus all prior reserves/margin. It cannot
  start a container. Root20 offline unittest passed, whitespace check clean.
- Actual stopped-node admission: available1306525696 bytes, required3543965696,
  approximately1246 versus3380 MiB. No node restart was attempted.
- Separate cached-build preflights failed before `docker build`:877.34 MiB,
  then822.96 MiB after a short authorized frontend pause, below1152 MiB build
  admission. No image was created, no PG stop occurred. Frontend restoredPID1184;
  backend/, proxied catalog and Core readiness returned200. Do not retry a build
  or node start on these stale insufficient samples.
- Existing fca image remains stale for current metrics source.65 stays Done;
  62/114/115/116/109 remain unaccepted, Core56 remains To do. The prepared Core
  statistics transfer map is unchanged; actual cutover has not occurred.
- **Local fixture credential incident:** one worker diagnostic printed complete
  node labels containing the synthetic k3d token. No value is copied here/Jira;
  public disclosure is not established. Inspect output must be allowlisted.
  Before reusing this cluster, validate a preserve-state credential replacement:
  K3s rotation alone leaves stale immutable Docker env/labels in k3d5.9 and is
  not a verified recovery path. No rotation, deletion or datastore reset occurred.
  This is not a Core or production credential. See Jira62 latest evidence.
- No worker/runtime allocation is active. Root did not close Codex/MCP or change
  Core data. All further stages require fresh capacity and confirmed prerequisites.

## Previous checkpoint: CORP-65 accepted, CORP-62 remains on memory hold

17.11.2026:19 of23 stage1 prerequisites accepted. Jira65 is Done, evidence10302;
62 final HOLD evidence10303. Root integrated Terra results; all assigned workers
finished. No runtime allocation or polling loop remains in progress.

- **Actual65 proof:**1 passed in39.90s. Own source PG node stopped before target
  creation; encrypted file outside source volume, separate key and signed journal.
  Full local recovery to fresh current barrier and survivor check27.8748021s;
  storage after targetReady9.9961394s; snapshot age atloss0.411467s. Restored
  prefix0 applied current HWM1: deleted subject absent, survivor preserved, bad
  or unavailable authority remained closed. This is local same-host/in-process
  acceptance, not production offsite disaster recovery or an operational SLO.
- Root independently checked private operation records through an allowlist of
  nonsecret fields. Source41aaf7ab-e7c5-4f59-a86b-05a8cbf43057 is deleted_after_proof;
  target5425b01c-d86e-4183-b0da-ee482edc7b79 has crypto/data/parity/privacy=true
  and exact-node cleanup flag. No test containers/networks/probes remain.
  Artifact, separate key and reopened journal remain private outside PG.
- Additional65 metrics: per-process pool gauges and current-DB waiting-lock
  aggregate, fixed labels/known flags. Root real PG+observability25passed/
  12subtests; positive waiting-lock regression1passed (>=1 while waiting,0 after).
  Contract+architecture20passed; contract/catalog artifacts match. Backup offline
  36passed/1live skip before its single successful explicit live run.
- User authorized closing applications. Root temporarily stopped Vite PID9316
  for65 and gracefully closed ordinary Firefox; Codex/MCP/Core stayed running.
  Frontend is now available again as existing PID16928 on5173, preserved without
  duplicate start. /backend/, main.tsx, proxied catalog and Core readiness200.
- Final host available1100.72 MiB, insufficient for62. Our exact synthetic K3s
  node remains gracefully stopped, with state/limits preserved; no app workload,
  authority, separate K3s DB, Secrets or migration Job exists. Last running-node
  guard was1596.69 available versus2482.53 required plus128 margin. No caps or
  reserves were weakened. Source/render Helm checks passed; CNI/runtime/scale
  and restart acceptance remain UNRUN.
- **Image warning:** service metrics source changed. Old fca runtime snapshot is
  stale; a006 schema is unchanged and its migrator use remains separately pinned.
  New image build/current-source validation is required before any62 runtime
  start; do not label the old image current. No build was attempted in low RAM.
- Core visit/day transfer map prepared and independently source-checked in
  core-statistics-transfer.md/Jira117 comment10300. No actual transfer, old writer
  retirement or change to user data occurred. Real entry is user/activity/visit;
  old daily marks do not encode minutes or courses. Progress stays Core authority.
- Next order after sufficient stable RAM:62→114/115→116→109 (65/86 done).
  Stage2 still additionally requires Core56, currently To do. No production,
  DNS/VPS/Core migration, git commit/push or unrelated user-data cleanup occurred.

## Previous checkpoint: resumed 17.11, CORP-65 in progress; CORP-62 memory hold

The owner explicitly resumed62/65 and emphasized moving existing Core visit/day
statistics into Analytics. Preparation is recorded in
[core-statistics-transfer.md](core-statistics-transfer.md) and Jira117 comment10300;
the real endpoint is `/api/user/activity/visit`. No Core statistics have been
disabled or transferred yet. Identity/access and authoritative progress stay Core.

- Terra admission: `analytics62_resume` xhigh (now terminal),
  `core_stats_inventory` medium (terminal source audit), `analytics65_resume`
  xhigh (backup proof) and `analytics65_metrics` medium (required pool/lock
  observability). These are admission-selected identities, not a separate model
  benchmark. The root owns verification/Jira; no user-owned chats were created.
- The existing Analytics PG was attested and started after Docker Desktop restart;
  its retained volume was preserved. Fresh62 admission: available1596.69 MiB,
  required2482.53 MiB, deficit885.84 MiB before the additional128 MiB margin.
  Jira62 comment10299 records this unsuccessful admission.
- Root authorized a sequential65 window: only our empty synthetic K3s server was
  attested and stopped gracefully. Node/datastore/network/kubeconfig remain intact;
  Core18041 health/ready200. No K3s DB/Secrets/Job/app Pods/import were created.
  Restart the node only after65 handoff and fresh admission.
- Helm3.21.4 host binary checksum verified from official release; default/privacy/
  migration/ingress lint and render passed, including negative config cases.
  This is template evidence, not running CNI or application acceptance.
- Backup worker is preparing a genuinely stopped own source node, encrypted file
  outside PG, fresh target and current independent signed deletion journal before
  positive readiness. Runtime has not started. The dedicated host-reachable57422
  test boundary must be attested; runtime Settings guards remain unchanged.
- A bounded observability addition for required pool/waiting-lock metrics is in
  progress. The previously frozenfca image must be rebuilt before it can represent
  changed service source. The old image can still be used solely as a pinned a006
  migrator with exact history verification; do not claim it is the new runtime.

## Previous checkpoint: stage 1 on hold, 18 of 23 prerequisites accepted

16.11.2026, branch `feach/Analitick`, HEAD
`f787672ff2639434f0be331d05def5d8f31b3c1e`. Terra workers completed their bounded
assignments. No worker is awaiting permission to mutate runtime resources.

- Live Jira readback: 57,58,59,60,61,63,64,66,69,77,78,79,80,81,85,86,113,427
  are Done. 62 and65 are PARTIAL; 114/115/116 and the overall109 gate are not
  accepted. Latest evidence comments:62 **10297**,65 **10295**,109 **10298**.
- The final K3s attempt stopped before database provisioning. No K3s DB marker
  or operation lock, authority, image import, Secrets, migration Job or app Pods
  were created. The synthetic node, API and system Pods remain running.
- The owned idle `k3d-tools` container was attested and stopped gracefully by
  full ID. Its volume/network and the server were preserved. The admission guard
  now rejects any additional running container belonging to the target cluster.
- One fresh budget sample after that stop: available **2072.40 MiB**, required
  **2127.68 MiB**, deficit **55.29 MiB**. Caps and the512 MiB host reserve were
  preserved. No polling/retry loop or further resource allocation is running.
- Root final focused checks after the guard change: **58 passed /33 subtests**
  in1.81s, architecture **2 passed**, contract export matches, pip check clean.
  The sandbox attempt hit the known Windows temporary-directory ACL error;
  the successful run used a fresh owned temporary path outside that restriction.
  The earlier complete backend run remains468 passed/5 explicit skips.
- Independent source review found no TTL, Core import, public catalog route or
  claim of a complete time pipeline/UI. Ingress/CNI runtime remains UNRUN.
  Core18041 `/health` and `/ready` returned200. Existing data and Git HEAD remain
  preserved; no commit, push or production change was performed.
- Facts and personal history have no automatic age deletion. Subject deletion
  remains mandatory. The planning update now carries the owner's retention
  decision explicitly; private `.local` credentials/kubeconfig remain ignored.
- Resume only after fresh admission of the existing exact cluster: finish62,
  then114/115; finish the remaining65 encrypted-restore/privacy and backup proof;
  then116→109. Even109 does not open stage2 until Core56 is accepted (fresh
  status: To do). No Core production cutover is authorized by this checkpoint.

## Previous checkpoint:86 done;62 and65 partial

-86Done10293,parent441Done10294. Latestrootfull468passed/5skipped/33subtests,
  90.96s:allPG/intake/backpressure/processor/replay/privacy/signedintegration/
  qualityoptins, backup3livecases+2imageoptionalremainexplicitlyskipped.
  Focused86root70pass, currentcatalog+signedpipeline+k3shelpers59pass21subtests.
- AllbackendpackagefrozeninclFixtureCatalog:globalANALYTICS_FIXTURE_CATALOG_JSON
  listofminimalCatalogSnapshotv2, explicitoperatorfixtureonly/defaultunavailable.
  private/metricsprocesscounters+Processoraggregate+LinuxRSS, noPIIlabels.
- Currentimagean1-current/an1-20261116-86 DockerindexIDsha256:
  fca46395a5c5982802808b208038acc95aad694d5899b637b081cf1542e5f1ee;
  platformmanifest062f6e8fd68dda87c8b96c78130233d2c6f09539e60a2bbe7b7de610177f0e81.
  All4roles actualnetworknoneUID10001Python3.12.16live200ready503/private
  metrics/LinuxRSS/gracefulPASS. Old7641snapshotstillMIGRATEONLYcompatiblea006.
-65PARTIALcomment10295: encrypteddump/freshrestoreparity, latewriteexpectedloss1,
  isolatednewPGnodeparity80.381s(sourcefixturegone, sourceblockedonresume,
  createportiononlysource-reviewed); node/networkcleanedterminal. Noactivebackup
  containers now. Fullphysicalnodeabsence/externalcopy4/currentprivacyopening
  afterencryptednode restoreunrun. Existinginfra/operations/knowi_backup policy
  reusedonlyinplannedseparateAnalyticsconfig; productionCoreopsuntouched.
- K3sclusterACTUALcreatedbyanalytics_network_release:knowi-analytics-01a1df75,
  K3s1.35.5-k3s1/noLB/oneserver2GiB/12757443, private.local/k3s-kubeconfig.yaml,
  contextk3d-knowi-analytics-01a1df75/NSknowi-analytics. Node/API/systempodsReady,
  separateAnalyticsPGnetworkattached; noCoreNetwork/contextchanges.
-62PARTIALcomment10296: phase-awareguardfix avoidsreservingalreadychargednode
  twice. Precreate2560MiBunchanged. PostguardRAWchargedNode/PGheadroom +authority
  remaining128 +transient256 +hostreserve512; verifiedcgroupv2/podnesting/swap0/
  pressure0/OOM0/usage<85%; actualfirstPASSwithnarrowmargin. Freshrecapturebefore
  allocation. FurtherstagedDB/authority/Secret/Job/Pod/CNI/restart/scalepending.
- ActiveNetworkworkerhasGOforstagedmetadata/DB/authority/Secrets; noJobsubmission
  beforeverifiedimageimport. k3dimportmustdirect(noextraToolsNode). Allothers
  terminalunlessnewfollowup. Helpersprepared:tools/k3s_database.py(PG7offline),
  k3s_authority.py(A34mocks), run_k3s.py/network phaseguard13tests. Rootreviewed
  budget/privatescope. Do notstartbackupcontainersparallelwithresourceworkloads.
- Corefreshhealth18041=200, branchfeach/AnalitickHEADf787672unchanged; noGitcommit,
  push/deploy/production/legacydatachanges. Core56freshJiraTodo, stage2NO-GO.

## Previous checkpoint after CORP-427

-427accepted/JiraDone; rootfull363passed/2optionalimageskips/1Starlettewarning
  withallPG/privacy/integrationoptins,86.59s. Atest-onlyCIpathfixthen19passed
  withTMPoutsideproject; allCLIprivatefilesremainunderowned.localfixturefolders.
- SignedactualHTTP+PGpipelineincludesuserintake,controlprojection,Querygate,
  signeddelete,lateevent403,oldlogicalsnapshotintoanotherfreshDB,currentexternal
  journalreplaybeforeopening. Missing/untrustedsourceclosed. Logicalsnapshot
  transplant isNOTencryptedbackup/pgdump/off-node/RTO/RPO proof(65pending).
- a006source+runtimefrozen; retainedsyntheticDBexplicitupgradeda005→a006ready,
  noauthorityconfigured/boundthere; Coreuntouched. Noactualcollection.
- Image427snapshot an1-current immutableIDsha256:
  7641bdc15665fabf0d95d3d946e58307c8a824b0afb10deb312627eb2366c8d1.
  All4rolesCollector/Processor/Query/Privacy Python3.12.16 UID10001 live200,
  ready503/defaultclosed/graceful passedwithnetworknone. Appstackstillstopped.
- A+Bworkers terminal; rootownsnewappPrivacyRuntime/factory/config/transaction
  guards andintegration tests. Sourceauthfixes reviewedPASS(source/offline),
  rootactualsseparate. Retentionnoneunchanged. ReadermetadataJSONisSecret.
- Nextreachabledeps:86and65 after427; 62stillPARTIAL/K3sresourceguardunpassed;
  114/115/116/109andrealstage2remainnotaccepted. Do notdeploy/push/commit.

## Previous checkpoint after CORP-85

- CORP-85 accepted, Jira10289 Done. Rootcombined allPG:86passed/2imageoptional
  skips; replay19/processor20/backpressure21/intake22/foundation4. Rootexplicit
  retainedsyntheticupgrade a003→a005 verifiedready; Core neverused.
- Actual image snapshot85:knowi-analytics:an1-current immutableIDsha256:
  0a7db2058eb635911cb31412838e802607d93b5a18cf903cc4baccefbd0cf3dd.
  Image3rolesPython3.12.16 UID10001 live200/ready503/gracefulpassed. Exactruntime+
  Alembichistoryprobe matched; oldcorp78 rejected. OwnProcessor/Query stopped
  andexactruntimePGrole sessions0 verified. Fullappstart refused; owned4apps
  exited, latesthostfree606–1221MiB<1792MiBappguard<2560MiBK3sguard.
- Sourcefull run had1WindowsProcessorstop timeout (232pass/86skip), causeunknown.
  Diagnosticagent5isolatedSIGBREAKstops0.141–0.235s,2focused24pass,finalfull
  233pass/86skip; no runtimefix or timeoutincrease. Testnowcapturesbounded
  nonblockingstdout/stderr/timingsonfuturetimeout. LinuximagestopalsoPASS.
- SharedauthDTO/portsnowcontracts/auth_v1.py; adaptercompat exports retained.
  Businessmodules don'timportadapters/app. Focused62pass, schemasmatch.
- Active427A analytics_contracts ownsprivacy_control_v1,privacy_authorityadapter,
  syntheticfixtureauthoritytool/tests/docs. Active427B analytics_postgres owns
  a006/privacy_storage_v1/PostgresPrivacyStore/tests/docs. Rootownsappprivacy
  gate/sync/config/factory/Databaseguardintegration. Noothercodewriters.
- New427source alreadymakes85imagefingerprintstale; rebuildonlyafter427freeze.
  Retained DBstaysa005untilthen. SourceauthorityprivateconfigMUSTBESECRET
  (includesreader_secret), neverpublictrustmanifest/ConfigMap/header/logs.

## Previous checkpoint after CORP-81

- CORP-81 accepted, Jira comment10286, statusDone. Final root realPG suite20passed;
  runtime loop12passed and realCLI health/shutdown1passed. Review scope issues
  fixed: candidate fairness, course-scoped as_of and normalized no-data including
  unrelated stale epoch. a004 source frozen, retained DB stilla003.
- Active CORP-85 owner analytics_postgres: migrationa005, replay storage/contract,
  processor/query minimal hooks and realPG replay tests. Same-version per-subject
  rebuild, bounded cleanup/scan, dirty query gate, lease-generation fencing,
  shared capacity/live reserve and final coverage under subject lock. No real KPI.
- analytics_contracts prepares427 protocol read-only; no implementation before85.
- analytics_scaffold added canonical local credential/trust setup tools,14focused
  tests passed; root has not yet integrated runtime use. No private data copied.
- Root added narrow .gitignore exception docs/analytics/*.md so source docs are
  reviewable; all credentials/state remain ignored. Older ignore notes below
  describe the earlier state and are superseded by this exception.
- Latest host-free memory2147880KiB<2560MiB K3s guard; Core/Analytics PG+Redis
  healthy, app stack stopped. Do not stop user applications or reconfigure WSL.

## Previous checkpoint after CORP-80

- Jira Done:57/58/59/60/61/63/64/66/69/77/78/79/80/113, parents438 and440.
  Comments10269–10284 contain scoped actual evidence;62 remainsPARTIAL,109 notGO.
- Latest full root suite:158passed/47explicitly skipped, oneStarlette warning;
  opt-in backpressure21passed; signedHTTP+PG intake22passed/1optionalimageskip.
  An earlier image-enabled intake run21passed. Source changes after that image
  (partial401/403 handling, budgets andrevisionpin) require nextbuild.
- Root fixed duplicate test module names by renaming intake_postgres/test_intake.py
  to test_atomic_intake_postgres.py. Root fixed test_postgres temporaryparentmkdir.
- Root added realPG expiry/tombstone-between-items tests: after firstcommit503
  unknownoutcome, sameID retry duplicate/accepted, freshdeletedrequest403.
- a003 sharedbudgets nowchecked BEFOREnewrawINSERT. Versioned eventdedup
  two-int advisorylock follows subjectbigint lock; legacybypassrace rollsbackquota.
- Root explicitly upgraded retained SYNTHETIC Analytics runtime a001→a003 using
  sentinel/owner/history/nonce guards. Core DB/user8 nevertouched. Publicmetadata
  files contain private roleURLs: do not echo .local/runtime.json.
- Local tools now use explicit --revision target andhistoryfingerprint; source
  migrations must be frozen for actualupgrade. `start` performs noDDL.
- Latest K3s admission2386MiB<2560MiB: no clustercreated. Dockerpart62 previously
  proved3roles live200/ready503, thenstoppedownappcontainers, PGkepthealthy.
- Current active81 owners: analytics_postgres ownsPGleases/migrationa004/public
  processingDTO+ports/tests; analytics_contracts ownsprocessorloop/handlers/config
  andprocessor-onlyfactorywiring/tests. No othercodewriters. Do not runactual
  retainedupgrade orbuildmigrationimage until a004writerterminal.
- Processorstore publishedclaimport: claim/renew/commit/fail/release with
  ClaimedBatch, SyntheticDelta, synthetic_delta andStaleClaim. Durable receipts
  are technical dedup forcompletedjobcompaction; notbusinesscourse tables.
- Root ownsCI/README/Jira/architectureintegration. Keyringcommonmanifest factory
  isconfigured butdefaultprivacy/catalog/fence remainclosed; actualcollectionoff.

Older chronology below is superseded by this checkpoint where it differs.

Updated 2026-11-16. User authorized implementation from CORP-57 in dependency order,
parallel gpt-6.1-terra work, and actual verification evidence in Jira.

## Baseline and protected state

- Branch `feach/Analitick`, HEAD `f787672ff2639434f0be331d05def5d8f31b3c1e`;
  existing uncommitted Core, frontend, course and workflow changes are preserved.
- Local Core `18041` and frontend `18541` remain running. Retained Core database,
  Docker project `knowi-ra-01a1df75` and its volumes are not Analytics test targets.
- Analytics gets a separate synthetic-only environment. No commits, pushes,
  production deployment or Core cutover are included in this execution.
- Core gate CORP-56 remains To do. Stage 2 requires both CORP-56 and CORP-109.

## Decisions

- Canonical plan: `docs/architecture/ANALYTICS_SERVICE_PLAN_20261115.md`, revision
  2026-11-16; CORP-438.
- User decision in this chat: detailed visits/time facts have **no automatic TTL**.
  Subject deletion remains mandatory; technical buffer/log/backup policies are
  separate and must not silently expire learning history.
- Three responsibility modules Collector / Analytics / PostgreSQL; independent
  collector / processor / query processes. One shared activity_facts table.

## Dispatch and dependency state

| Task | Owner | State | Scope |
| --- | --- | --- | --- |
| CORP-57 | analytics_boundaries + root | accepted / Jira Done | docs/analytics/architecture.md and architecture-checks.md |
| CORP-57/113 source snapshot | analytics_infra_snapshot | terminal PARTIAL | Read-only source audit complete; sandbox runtime reads denied |
| CORP-58 | analytics_contracts | accepted / Jira Done | Versioned executable contracts and fixtures |
| CORP-58/69 review | ra_backend_review | terminal PASS after fixes | Integer-ms and restore-handoff corrections |

Current after integration: CORP-57/58/59/60/61/63/69/113 and parent438 are Jira Done. Comments10269–10277
contain scope-specific evidence, including limitations. CORP-58 now18 contract
tests; integer interval1001ms regression fixed. CORP-59 reference9 tests,
catalog3, scaffold/architecture19, privacy14. Last full run60 tests was before
three final privacy cases; focused privacy14 then passed. Exporters match.

Active owners:
- analytics_postgres: CORP-63 terminal COMPLETE, root accepted; files free for
  subsequent explicitly assigned work.
- analytics_network_release: CORP-64 new chart/GitOps/local app compose/deployment
  checks. Existing Core chart/GitOps/bootstrap/CI untouched.
- analytics_infra_snapshot: CORP-113 terminal COMPLETE, root accepted profile only.
Root owns PG compose, credential metadata, integration, Jira and further dispatch.

Latest actual checks: root non-opted-in full suite71 passed/4skipped (live PG and
image checks opt-in), separate live PG+image runner5 passed. Sandbox tmp_path
PermissionError reproduced even with local basetemp; narrow elevated rerun passed.
No functional expectations weakened. test_postgres.py now uses a guarded unique
Analytics-local temporary path. Starlette httpx deprecation warning remains.

Helm3.19 lint --strict and render passed; output .local/chart-rendered.yaml.
CORP64 worker actual Docker/Traefik routing smoke passed with synthetic fallback
Core/Mentor, internal network, no DB, memory limits, no direct app port bindings,
404 denial of near-miss/control/probe paths, no wildcard CORS. Final canonical
pin/temp safety corrections pending worker terminal. This is not CNI or real Core
business regression evidence.

Root added separate .github/workflows/analytics-ci.yml for contracts/image/Helm;
it neither publishes nor deploys and has not been run remotely. Local live DB
runner remains separate, explicitly skipped by default in this CI.

K3d5.9.0 downloaded from official release, SHA256 verified against release digest,
stored only under .local/bin; version shows defaultK3s1.35.5-k3s1. No cluster was
created: last host-free-memory snapshot980268KiB is below safe full-profile
admission. Do not change WSL settings or stop user processes to force a PASS.

Native admission selected gpt-6.1-terra xhigh for analytics_boundaries and medium
for analytics_infra_snapshot. Tool admission is model evidence; no independent
engine benchmark is claimed. Root owns acceptance and Jira writes.

## Actual observations

- Jira CORP-534 Done verified. CORP-57 execution-start comment `10268` saved.
- CORP-57 root acceptance saved as comment `10269`, transition31 returned Done.
  Structural checks: ownership, ports, C4, source boundaries, partition/fencing,
  independent deployment/resource matrix, 13 links, Markdown and whitespace.
- Elevated read-only Docker check: Docker 29.9.2, 1 CPU, 6217981952 bytes RAM;
  only retained PostgreSQL/Redis containers currently running, both healthy.
- Host free physical memory 3447260 KiB at snapshot; not a performance guarantee.
- Local Python 3.14.6; existing Core image source uses Python 3.12.16. Analytics
  will pin its own dependencies and test its actual selected runtime.
- Context7 checked Pydantic 2.13.4 strict JSON validation and FastAPI lifespan /
  OpenAPI generation. Contract validation and runtime durability are different.
- Separate Analytics development venv created using local Python 3.12.10; direct
  pins Pydantic2.13.4 / FastAPI0.139.0 / uvicorn0.50.0 / pytest9.1.1 / httpx0.28.1.
  pip check passed. Bootstrap selected transitives are not yet the canonical lock.
  Windows sandbox denied ensurepip in C:/Temp; narrow elevated bootstrap completed
  using Analytics-local temporary directory, without touching Core environment.
- Local kubectl has no configured contexts; k3d/Helm host CLI absent. Helm image
  3.19.0 exists, as do Python3.12.16, PostgreSQL16/17 and Traefik3.7.15 images.
  A separate local K3s test cluster will be needed for CORP-62/116 runtime proof.
- Skeleton Docker build passed: knowi-analytics:an1-local, image manifest list
  sha256:9a546dde6681b4687d71fa522755e5045300e6c6b25d1b8b9294165887ea32e7.
  check_image.py executed collector/processor/query separately: network none,
  read-only filesystem,128MiB/0.5CPU,UID10001,Python3.12.16; live200/ready503 and
  graceful lifespan completion. This proves skeleton lifecycle, not runtime DB.
- New isolated PG healthy: project knowi-analytics-01a1df75, container
  knowi-analytics-01a1df75-analytics-postgres-1, loopback57392, ownvolume/network
  knowi-analytics-01a1df75-net. PostgreSQL16 maxconn40/shared_buffers32MiB,
  memory384MiB/cpu0.5. Credential values only in ignored .local JSON/env, no logs.
- Root repeated source package check: DB dependencies now installed in Analytics
  venv (SQLAlchemy2.0.51/asyncpg0.32.0/psycopg3.3.8/Alembic1.20.1). Postgres
  worker owns final pinned resolution; do not concurrently edit its lock/app files.

## Pending

Complete and verify 57, then 58; after 58 run independent 59/60/69 and scaffold61
according to Jira links. Privacy/SLO details must be concrete before runtime tests.
No runtime Analytics PASS or infrastructure gate acceptance has been recorded.
