# Core ↔ Analytics synthetic acceptance

Status: **synthetic a007–a010 full-chain functional run passed on 2026-11-17** after the
integration owner's runtime GO. This is not a production, coverage, cutover or
performance acceptance. Neither the current a006 acceptance nor retained
databases were changed.

On 2026-11-17 fresh full a007 → a008 → a009 → a010 runs completed
with exit 0, including cleanup. The versioned Core addon installer and the
proposed Analytics migrations were applied only to its new UUID databases. In
addition to the preceding checks, real HTTP and PostgreSQL processes covered
browser-only activity, historical backfill and drain, report totals/details and
interval edges, dirty-generation handoff between two worker processes, and
privacy purge of facts, proofs and buckets. A logical snapshot restored with a
new externally recorded run identity remained closed until the current signed
journal and a new scan/drain; the old-identity worker made no changes. The
fresh-target copy temporarily disabled only the derivative dirty-bucket trigger
inside its transaction, re-enabled it before commit, and checked it after
commit. Independent checks found all three test databases and their Analytics
roles absent, no remaining test DB sessions, and ports 18151–18159 free. The
latest run used the promoted canonical migration versions and exited 0.
This is functional synthetic evidence, not performance or production evidence.

The subsequent fresh no-hold a010 full-chain completed with exit 0. It checked
the independently calculated mixed Core/browser profile and calendar, HTTP
SQL guard, profile and quota parity across the outbox-only switch, Query outage
before quota mutation, and a buffered-response race after synthetic numeric ID
reuse. The outage left the quota unchanged; the stale race response was rejected.
The runner verified its Redis prefix empty and cleaned the fixture; a separate
read-only check found the exact Core and two Analytics databases and twelve
Analytics roles absent, no target sessions and no listeners on 18151–18159.
The race runs after a browser hold release so its review roster stays stable.

The first subsequent no-hold attempt stopped at the profile parity assertion.
The Core fixture had four legacy activity dates, while the earlier B2 scenario
had also recorded a browser-only date for that same subject. The a010 bridge
deliberately unions these days, so the original legacy-only expectation was
incorrect. The failed run was reconciled: delivered outbox, no quota mutation,
no remaining sessions/listeners, and exact owned databases, roles and Redis
prefix cleaned. The corrected oracle independently unions Core dates and the
accepted B2 intervals in Moscow time, checks calendar timestamps/NULL and
summary, then compares Analytics profile before and after outbox-only mode.
This correction was validated in the subsequent full-chain run described above.

On the same day, a second independent fresh run applied proposed a007 → a008 →
a009 and Core 001 → 002. It passed real P1 catalog publication and lost-reply
reconciliation, current-digest/access checks, all six a007 HTTP scenarios,
pending-to-verified proofs and processing receipts, signed G1 self/admin cuts,
late-fact invalidation and above-cut closure, and a PostgreSQL roundtrip above
2^31. An instrumented Core process first demonstrated that its request-scoped
detector rejects old activity SQL, then verified analytics self/admin reads and
outbox-only visits without that SQL. Bounded preparation and replay returned to
mirror with the tested dates and NULL preserved. A proof-bearing a007–a009
logical snapshot was closed until the current independent journal, after which
the deleted subject's fact, projection and proof remained purged.

The second run's functional checks all passed, but its command exited 1 when
Windows denied deletion of a file in the private fixture directory. Before any
retry, read-only reconciliation confirmed the three UUID databases and twelve
roles were absent, no scenario listener or recorded process remained, and its
Redis prefix was empty. The exact remaining private directory was then removed
after owner verification. Cleanup now retains its owner marker until all other
files are gone and handles bounded transient lock failures; its transient and
persistent lock paths have offline tests. No second live run is claimed for this
cleanup change at that point; the subsequent a010 run above completed cleanup.

An initial a010 full-chain attempt passed the versioned Core addon install,
a007–a010 schema/role setup, P1 catalog lost-reply reconciliation, a real
browser-fact backfill and Processor drain, concurrent Core revision/lost-reply
checks, and the six earlier HTTP cases. The host-memory guard then stopped only
the owned processes during proof staging. Before cleanup, the Core raw outbox
was fully delivered with no uncertain rows; six proofs were staged but not sent,
and Analytics had no proof rows. Both test database sentinels and owners were
verified, there were no remaining DB connections or scenario listeners, and the
owned Core/Analytics/restore databases, roles, Redis prefix and private fixture
were removed. This interrupted attempt is not an a010 acceptance result.

The runner reported schema and role grants, concurrent old/live revision and
lost-reply receipt reconciliation, all six HTTP scenarios, and the a007 logical
restore privacy barrier and purge as passed. Core used Python 3.14.6, Analytics
Python 3.12.10; the measured available host memory after process start was
2801 MiB. After completion, independent read-only checks found zero databases
with the three UUID names and zero associated Analytics roles, and no listeners
on 18151–18157. The runner also verified its Redis prefix was empty. The first
attempt failed before HTTP startup because the Analytics bootstrap import path
was missing; its Core target contained only the sentinel, and was reconciled
and removed before the successful fresh attempt.

Offline checks from `services/analytics`:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/cross_service/test_chain_offline.py tests/cross_service/test_guards.py tests/cross_service/test_hold_offline.py tests/cross_service/test_live_http.py -q --tb=short -p no:cacheprovider
```

The live tests skip by default. The explicit runner needs Core Python 3.14,
Analytics Python 3.12, the designated local PostgreSQL clusters and Redis DB 13:

```powershell
$env:ANALYTICS_CROSS_SERVICE_GO = '1'
.\.venv\Scripts\python.exe -B tools/test_cross_service.py --run
Remove-Item Env:ANALYTICS_CROSS_SERVICE_GO
```

The full-chain fixture uses `--run --chain --a010` for the a010 scenarios;
`--run --chain` stops at a009. Neither enables browser hold unless a bounded
`--hold-seconds` value is explicitly supplied. Migrations are applied
only to its new test databases; it does not activate the existing migration head.

The source-prepared opt-in `--run --chain --a010 --a011` additionally enables
the real day worker and day Query using one shared dataset run ID. Its intended
scenario accepts signed browser events through Collector, waits for bucket and
day projections, reads a complete Moscow day, and repeats after a late event.
The logical restore checks an empty, exact-revision target and invalidation
trigger, rebuilds derived day state under a new run and checks privacy purge;
an ambiguous restore remains recorded for reconciliation. This is a synthetic
logical restore, not the separate encrypted-backup workflow. These a011
full-chain scenarios are prepared in source and are not live acceptance evidence
until executed under a separate runtime GO. Default a010 mode is unchanged.
Old-run claim checks here cover the a010 bucket and a011 day projections only.
Generic processing jobs, leases and replays retain the existing logical snapshot
and lease-expiry recovery; this scenario does not establish a new generic-claim
restore guarantee. The separate encrypted synthetic backup remains a010-only.

The opt-in Linux publisher scenario adds `--publisher --publisher-image
sha256:<full-local-image-id>` to the full chain. It rejects mutable image tags and
browser hold. Docker Desktop must be available to the runner, and the exact
image must already exist locally. The fixture records ownership before creating
its dedicated network and containers. A small local relay preserves the
publisher's loopback-only HTTP endpoint policy; the publisher receives only the
synthetic database and service endpoints. The bridge does not enforce a general
egress firewall. Private signing keys are mounted read-only, and the publisher
runs as non-root with bounded CPU, memory and PIDs, a read-only root filesystem,
no capabilities and no-new-privileges. Its first and restarted instances must
each exit after SIGTERM with `docker wait` reporting zero and the stopped log;
timeout or ambiguous ownership preserves evidence for reconciliation. This is
separate from the native Windows forced-stop scenario and does not change it.

For either scenario, `--hold-seconds N` keeps the owned services
alive after all assertions, with `N` between 60 and 3600. The default remains
zero, and there is no automatic renewal. The process prints the
non-secret operation UUID and an expiry. It polls for a private token-bearing
release request and closes on explicit release or expiry. From a second shell:

```powershell
.\.venv\Scripts\python.exe -B tools/test_cross_service.py --release <operation-uuid>
```

The release command reads the token from the owned private fixture, without
printing it; do not copy it into a command line. The holding process checks all
essential child processes, reconciles its Core outbox after browser activity and
only then starts cleanup. A failed reconciliation preserves the target for
inspection. Other fixture credentials stay in the same private fixture; a
deleted identity must never be used for browser work.
Browser admin and student use separate synthetic accounts; the browser leader
uses the already configured synthetic leader because each course has one leader.
Hold fixtures include a small synthetic practice task with starter code; its
catalog and public task schema were checked offline. A rendered editor and
three-role browser interaction require separate live verification.
In a chain hold, the deletion checks use a separate synthetic student, preserving
student 7 for the browser. Before the hold the runner checks admin 1, leader 2,
student 7 via GET /api/user/me, verifies the learner's practice material over
HTTP, and checks that student 7 has no Core activity day. The operation record
includes the holder PID and expiry for the review launcher. These hold-specific
checks passed over live Core HTTP/DB in the 2026-11-17 a010 hold.

For a pagination review only, add `--browser-pages` to
`--run --chain --a010 --hold-seconds 3600`. The private backend catalog then
includes 25 additional synthetic lessons; the separate extra course and the
browser student's original lesson remain intact. A real user-scoped Collector
grant submits 25 non-overlapping synthetic intervals, and `HOLD_PAGES` reports
the isolated interval, independently expected totals, and the verified 20+6
breakdown rows. These IDs are report fixtures; browser reviews should open only
the original learner lesson. The option cannot be combined with a publisher run.
The a010 chain hold alone also prepares a second fixture course
`backend_extra` with `extra-track` / `extra-lesson-1` and a distinct source file.
It has no configured leader; leader 2 remains scoped to `backend`. After
publishing both catalogs through the guarded local publisher, the runner uses
Core-issued collect grants for student 3 to submit a course-open and a two-second
segment in each course. The selected-subject oracle checks one distinct visitor
globally and in each course for the same short interval, and checks leader scope.
It also prints a sanitized one-hour `HOLD_BASELINE` before browser activity.
On 2026-11-17 both catalogs were published and the selected-subject and leader
scope assertions passed against live HTTP and PostgreSQL. For a common one-hour
window, the runner observed global 3 visitors/149000 ms, `backend` 3/147000,
`backend_extra` 1/2000, leader's `backend` 1/92000, and fresh student 7 0/0.
An independent read-only request for the Moscow UI day returned the same totals
before the student logged in. In the later UI observation, no student 7 B2 facts
were recorded before the hold expired; this fixture result is not a tracker PASS.
The hold expired normally and the post-hold race passed; cleanup was verified
independently for the exact databases, roles, sessions and listeners. The
ordinary no-hold full-chain fixture still has one course.

The first browser review reported that the global legacy platform overview was
unavailable even though the course overview and new Analytics reports worked.
The original hold did not record the legacy overview's source proof vector before
review, so its exact cause was not recorded in that run. Source inspection shows that the
course overview uses Core directly, while the global route requires a verified
proof at the current revision for every active Core lifecycle. The fixture's
replacement lifecycle is created before its original proof scan; a later
outbox-only scenario changes student 3 after that scan. The next hold now records
the global/course HTTP status and sanitized per-user proof vector, then stages
and relays a fresh snapshot for the changed subject and requires verified current
coverage and a successful global overview before browser review. In a subsequent
fresh hold the live diagnostic observed global 503 and course 200: student 3's
Core revision was 3 and its verified cut was 1, while the other four current
lifecycles, including replacement user 4, matched. After explicit staging and
verified relay, student 3's cut became 3 and the global and course responses
were both 200. The hold later expired normally and its exact resources were
independently verified cleaned. This manual fixture refresh is not evidence of
an autonomous publisher or its recovery behavior.
Only the full-chain hold uses the disposable review password
`Analytics-Local-Review-20261117!` for admin 1, leader 2 and student 7; other
fixture passwords are randomly generated. This password is for synthetic local
accounts only.

The flag records the integration owner's runtime GO; it does not grant one.
Do not set it before that GO. No command output contains tokens or DB URLs.
Before network mutations, the runner requires free ports, creates new UUID
databases on 57382/57392, and checks owner and unique sentinels. A Windows job
owns the HTTP child processes and their descendants; listener PID membership is
checked before use. Core, Collector, Query, Privacy and Processor bind to
18151–18155. Independent authority and the lost-reply proxy use fresh loopback
ports. Synthetic signing material and the operation record stay under
`services/analytics/.local/cross-service/<uuid>` outside public assets.

Core is migrated through c528, then its versioned addon installer is applied only
to its new DB. Each of two new Analytics DBs is migrated through a006 and has the
proposed a007 loaded explicitly; a007 is not activated as the existing head.
The a007 status role is separate and NOINHERIT, with only the migration's
purge-check columns; collector, processor, query and privacy retain their own
roles. The course fixture is generated separately, its `content_version` is
computed and validated with Core, and no canonical course is edited.

The runtime assertions cover signed Core backfill and a concurrent live
revision, a real collector commit followed by a deliberately lost reply,
receipt reconciliation without duplicate facts/jobs, and receipt survival after
completed-job compaction. The HTTP checks compare calendar, original date/NULL,
Moscow completion day, streak and distinct admin metrics, with Core-issued user,
leader and admin grants, full synthetic roster and numeric target mapping. Two
overlapping B2 intervals require 90 seconds total (30 theory, 60 practice).
Trusted progress remains unavailable until scoped coverage and freshness are
proven; time must never fabricate completion.

The deletion assertion requires pending 202 before the relay, then an independent
signed journal entry, an applied prefix, zero subject data in the listed fact,
job, receipt and projection tables, rejection of late transfer, and only then
204. A repeated delete after numeric ID reuse must preserve the replacement
account. A logical pre-deletion snapshot containing a007 facts and projections
is transplanted into the second freshly migrated Analytics DB. Its new Query
process must stay closed until the current independent journal is applied; after
recovery the subject remains fenced and purged. This is not an encrypted backup,
off-node restore, performance or RTO/RPO test.

On complete success the runner stops only its child processes, removes only its
Redis prefix, and checks owner and sentinels before removing the three UUID DBs
and the private fixture directory. On a failure, timeout or unknown side effect,
it stops its children and preserves the databases and private record for
reconciliation; do not rerun deletion or guess a cleanup target. The runner
prints the non-secret private operation UUID for recovery. A partially created DB
or role may require individual reconciliation using the recorded intended name.
No automatic cleanup is attempted when the sentinel or ownership cannot be proven.

Schema rollback after accepting Core facts is unsafe without a separate data
conversion plan. Old-writer/new-schema retirement, declared complete coverage,
production rollout and cutover remain separate gates.
