# Local Personal Analytics activation

Request17.11.2026: connect actual lesson measurements to the existing course
Statistics / My data panel on localhost5173. Implementation is authorized;
production, role changes, legacy dataset replacement and historical fabricated
time are outside this task. Account u226 remains an administrator. New collection
is only for role=user; confirm the account used in the screenshot separately.

## Runtime contract

- Preserve protected main Core c528 on18041 and Redis14/session namespace.
  Existing visits, calendar, progress and subscriptions keep their current path:
  legacy read off/write mirror, Core transfer delivery/proof off. No Core DDL.
- Fresh persistent Analytics DB analytics_live_<instanceUUIDhex32> on57392,
  six least-privilege roles; role names including _transfer_status fit PG63.
  Never reuse retained a006/test DB or permit fixture cleanup of this database.
- Explicit issuer_mode=local and real_collection=true, numeric loopback only.
  Old synthetic defaults/guards remain. No event TTL, no automatic data cleanup.
- Private root services/analytics/.local/local-live/<instanceUUID>/;
  owner.json exact fields: version1,mode local,do_not_drop true,instance_id,
  core_instance_id,dataset_run_id,authority_id. Canonical UUIDs; root name is
  instance_id. Other runtime/process/credential metadata stays in separate files.
- G1, P1, authority-response and deletion-command signing keys are independent;
  authority reader and Core source HMAC are separate random32B secrets. Private
  files are user+SYSTEM only, no links. Never log key/DSN/token/contact data.
- Authority is a separate process on18046 with persistent authority.sqlite,
  explicit local mode and fixture_mode=false. Init is exclusive, serve never
  recreates a missing journal. Keep it outside Analytics DB snapshots.
- Local compact bundle may cohost Collector18042, Query18043, Processor18044
  and Privacy18045, each with own app/settings/DB role/pool. Existing separate
  role CLI remains. Cohost shares a failure domain; it is not scaling evidence.
- One a010/a011 run UUID, real workers/backfill. Frontend activation occurs only
  after privacy, schema, backfills and all roles are actually ready.

## Core deletion source and barrier

Core426 already has durable account_deletion_events. The new internal source
uses fresh READ COMMITTED READ ONLY primary transactions, validates pinned DB,
Core426 schema/functions/enabled trigger, and returns all events sorted by
subject_ref, bounded1024. Overflow or missing trust gives unavailable.

GET /api/internal/analytics/deletions?nonce=<UUID>&instance_id=<CoreUUID>.
Request header X-Analytics-Request-MAC is lowercase hex64 HMAC-SHA256 over
canonical JSON {version:1,instance_id,nonce}; request domain bytes are
pymentor:analytics:core-deletions:request:v1 followed by NUL.
Response exact fields: version,instance_id,nonce,as_of,events,mac. Event exact
fields: subject_ref,deleted_at. Response MAC covers all fields except mac with
response domain pymentor:analytics:core-deletions:response:v1 followed by NUL.
Canonical JSON is UTF8/sort_keys/compact separators/noNaN; timestamps UTC
microsecond Z. Secret is canonical unpadded base64url32B in a private file.
Client bound256KiB/1s; no cached/fallback snapshot.

Relay uses producer core-local and deterministic control UUID5(CoreUUID,
analytics-local-delete:+subjectUUID), epoch1 and original deleted_at timestamp.
Fresh producer JWT60s; retries keep exact ID/payload. It does not need Core DDL
or a deletion delivery table because source events and authority journal persist.

Collector/Query guard checks source snapshot plus exact control/hash/subject/
epoch/phase=complete, tombstone and applied journal watermark, with current
signed authority barrier. Status DB connection is SELECT-only transfer_status
on the SAME Analytics database, pool1/overflow0. Apply guard before and after
user transactions; do not gate the relay or privacy worker on their own purge.
Core outage/new undelivered delete fails closed. Requests begun before deletion
commit retain the explicitly bounded in-flight-response exception. Role revoke
still follows existing grant TTL; no instant-revocation claim.

Do not rewind Core source and authority journal together. Privileged operator
mutation of deletion history is outside the append-only runtime contract. One
host is not proof of physical disaster independence or production readiness.

## Catalog and frontend

Core grants/catalog enabled under validated local policy. New parallel catalog
header X-Analytics-Collection-Mode:parallel is independent of the older strict
legacy-cutover header. Publish actual CourseRuntime snapshot through a guarded
local publisher; receipt binds dataset, namespace, Core instance, collector URL
and producer fingerprint. Unknown outcome stays pending until reconciliation.

Tracker=true/cutover=false means explicit parallel collection: old visit path
stays enabled, Tracker only for user with verified parallel catalog. Reports
and personal-measurements flags control reading separately. Preserve UI/demo,
current progress and permission boundaries. Refresh reports on returning to the
account panel with bounded follow-ups for asynchronous projection.

## Acceptance scope

1. New-mode and old-synthetic regression tests; MAC/nonce/instance/overflow,
   missing trigger/replica, wrong DB/role, loopback/path/link/ACL rejection.
2. On disposable local-mode data: delete between guards, lost ACK duplicate,
   Core/authority outage and old DB restore with current independent journal.
3. Protected Core preflight/counts/schema/trigger and unchanged fingerprints;
   create ONLY fresh Analytics DB/roles/root, never seed or reset main Core.
4. Actual local user lesson -> B2 ACK -> fact -> real projection -> same-subject
   course report -> My data rendered numbers. Staff never collect; personal
   data never replaced by demo/zero on error. Preserve real progress.
5. Restart and confirm persistence, capture safe evidence/Jira. Keep runtime
   available for the user; stop commands preserve DB/journal/private metadata.

## Observed local acceptance, 2026-11-17 16:29 UTC

Local parallel collection is enabled on http://127.0.0.1:5173/backend/.
Persistent instance `1742f31e-be55-40fe-8e76-d0d98adf2d65` uses its own
`analytics_live_1742f31ebe5540fe8e76d0d98adf2d65` database, six roles and
independent private authority journal. No retained Analytics dataset was reused.
Core keeps its existing DB, schema and Redis. One owned QA learner was registered
through the normal HTTP API with SMTP disabled; existing accounts were not
changed. This is actual local learner collection, not a synthetic benchmark.

- Core 18041, Collector 18042, Query 18043, Processor 18044, Privacy 18045 and
  authority 18046 are listening on loopback. The four role readiness checks
  verified trust, fresh Core deletion source and projection readiness.
- Both projection backfills are `complete` with the expected run UUID. The
  actual role SQL check found 14 facts for one subject/course, one ready bucket
  and one ready subject/day source. Core's authenticated deletion snapshot was
  successfully read over HTTP, with zero existing deletion events.
- Actual full catalogs for `backend` and `backend_copy` were accepted with
  signed receipt reconciliation. The initial POST 400 exposed Cyrillic legacy
  aliases rejected by the reference contract; bounded Unicode aliases now pass
  without relaxing canonical IDs. The saved pending message was validated and
  retried with the same fingerprint, then confirmed accepted.
- Web flags: tracker/reports/personal measurements enabled, legacy cutover
  disabled. Collector and Query have their own proxy targets. Core visits remain
  in mirror/legacy-read-off mode; this is not complete Core statistics offload.
- An isolated Chromium session used normal learner login, opened a lesson and
  its practice editor, and received HTTP 200 B2 ACKs. Personal report HTTP 200
  returned `active_ms=109803`, `theory_ms=45612`, `practice_ms=64191`, one visitor
  and three detail rows for the same actor/course. The rendered **My data** view
  showed 1 minute, theory under 1 minute, practice 1 minute and real progress
  9/280. Full browser reload and returning to My data preserved these values.
- Coverage remains `unknown`; missing historical days are not zeros. Study-day
  count and daily average remain unavailable until coverage is established.
  Staff collection remains disabled by policy. No staff login/browser claim
  is made for this run.
- Offline evidence from the bounded workers: Core local checks 55 passed;
  Analytics local runtime checks 46 passed; private writer/preparation 19 passed;
  catalog 22 passed with schema export check; frontend focused checks 61 passed;
  full Web TypeScript no-emit check passed with 512 MiB heap.

Safe browser evidence: `.local/analytics-browser/personal-live-1742.png` and
`personal-final-snapshot.txt`. Private keys, grants, cookies and QA credentials
remain only in the protected local instance and are not part of these records.

The isolated local-mode deletion/lost-ACK/restore/outage acceptance matrix is
still outstanding. Existing synthetic privacy evidence does not establish that
matrix for this new local profile. K3s load, production rollout and full Core
statistics offload remain separate open gates; no Jira completion is claimed.
