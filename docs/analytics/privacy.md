# CORP-69: privacy-v1

Version 2026-11-16, dependency CORP-58 accepted. This is an executable policy /
protocol specification. Database deletion, concurrent locks and clean restore
are implemented and measured later in CORP-427/65/116. The existing Core audit
is preserved. New staff-performance metrics or staff-operation logging are out
of scope. First-stage data, issuer and journal are synthetic.

## Purpose and access

| Data | Purpose | Read access | Authority |
| --- | --- | --- | --- |
| Shared activity_facts | Student visits and theory/practice time | User: self; leader: assigned course and signed personal-booking roster; platform admin: student data with course/period filters | Collector owns immutable accepted payload |
| Confirmed progress | Actual Core completion facts | Same student/course/roster restrictions | Core; never inferred from browser time |
| Course overview cohorts | Daily course-wide visitor and completion counts by current subscription status | Platform admin or leader of the assigned course; aggregate counts only | Core authorizes the course; Analytics enforces deletion fences for visitor facts |
| Minimal catalog history | Stable product/lesson/task identity and labels | Authorized report context | Core versioned export |
| Subject fence and deletion journal | Prevent stale commit, late replay and resurrection after restore | Privacy control and narrowly permitted guarded reads | Independent trusted control authority |
| Operational logs | Availability, bounded retries and failure diagnosis | Operators only | Analytics technical runtime |

Core alone maps accounts to fresh opaque lifecycle UUID subject_ref. A new
registration gets a new UUID even if numeric user ID/email is reused. Browser
learning session_id is independent of authentication session identity. No email,
name, contacts, cookies, tokens, IP, UA, referrer/query strings, code, stdout,
answers, keystrokes, messages or screen recordings enter facts/logs. Core supplies
an allowed display name to the future UI separately. Common subscription or
search-to-grant does not enlarge Analytics report scope.

The course overview cohort exception was explicitly selected on 2026-11-19: it
includes learners without personal bookings and does not authorize identifiers,
individual reports, lesson filters, or access to another course. Counts can be
small and may permit inference in a small cohort; the course-wide aggregate is an
intentional exception to roster-only access. Visitor coverage is unknown, so a
missing observation is not a confirmed zero. Subscription grouping uses the
current status at report time, not the status at the historical event.

## Retention and bounded technical state

| Category | Policy v1 | Deletion / recovery rule |
| --- | --- | --- |
| Detailed visits/time, personal history, trusted progress | No automatic TTL, chosen by owner in chat 16.11.2026 | Subject deletion removes personal facts and their visible contribution |
| Minimal catalog history | No automatic TTL | Retain identity history needed to interpret facts; exclude personal data |
| Derived outputs | No independent truth or mandatory per-course tables | Rebuildable, invalidate with subject epoch; cannot outlive source permission/deletion |
| Completed technical jobs | Compact after 7 days | Original immutable fact retains dedup identity; unfinished/quarantined work is never silently acknowledged or expired |
| Browser buffer | Memory only, 24 hours / 200 events / 512 KiB, first reached bound | Optional unacknowledged telemetry drops oldest with partial-quality counter; no fabricated ACK. Trusted control/Core facts never use this drop policy |
| Sanitized operational logs | 14 days | No raw request body/bearer/PII. No new employee business audit |
| Analytics backups | Latest 4 restore-verified copies | Failed verification preserves prior copies; successful rotation removes only older Analytics copies. Independent from Core backups |
| Subject tombstones/epochs, authoritative deletion journal | No automatic TTL | Minimal opaque UUID/epoch/control sequence, no account mapping; required because facts and allowed old snapshots have no age expiry |

No timer silently deletes learning history. Backups may still contain encrypted
old facts until verified-copy rotation; restoring any copy cannot serve them
before replaying all authoritative deletions. A journal copied from that backup
does not establish the current deletion watermark. Loss/unavailability of the
independent journal leaves recovery closed. Four files on one disk are not HA.

## Grants and revocation

The accepted CORP-58 contract uses asymmetric EdDSA, algorithm/key/issuer/audience
allowlists, NumericDate, and independently scoped credentials. Browser grants
remain in memory and Authorization headers, never URLs or telemetry. Synthetic
issuer is separate from Core; real Core issuance belongs to stage 2.

- Collection TTL <=300 s, reports TTL <=60 s. Zero expiry leeway; synchronized UTC
  clocks required. Issuance/refresh checks authoritative Core role/course/roster.
- Logout/role/assignment/roster change stops refresh. Existing signed grants can
  remain usable until expiry: collection at most300 s, report access at most60 s.
  This bounded contract is not immediate Core revocation; no copied session secret
  or synchronous Core/Redis lookup is introduced.
- Normal signing-key rotation keeps old verification key for at least300 s after
  last issuance plus deployment propagation; compromised/unknown/removed keys are
  denied immediately on verifier configuration refresh. Never accept alg from an
  untrusted token as verifier policy. Real rotation propagation is tested later.
- Subject deletion is stronger than every still-valid grant. Local fence check
  protects admission, query and worker commit after the local Analytics fence
  transaction commits. An earlier Core deletion commit is not this boundary:
  Core must keep cross-service deletion incomplete until Analytics completion ACK.
  A grant cannot override a tombstone. Explicit finite roster or scoped-page grant
  is not a population estimate; no silently truncated leader overview.

## Transaction and deletion sequence

```mermaid
sequenceDiagram
  participant A as Trusted control authority
  participant P as Privacy coordinator
  participant D as Analytics PostgreSQL
  participant W as Processor / Collector
  participant Q as Query
  A->>A: Persist deletion sequence + subject lifecycle independently
  A->>P: Retry same control ID until acknowledged
  P->>D: Begin; lock subject fence FOR UPDATE; increment epoch/tombstone
  P->>D: Delete facts and personal/derived state; invalidate shared generation
  P->>D: Commit only after required module effects
  W->>D: Lock subject fence, then partition; verify epoch + lease generation
  D-->>W: Tombstone or stale generation: reject commit/ACK
  Q->>D: Conflicting subject fence read lock; bounded scoped read
  D-->>Q: Deleted lifecycle excluded; dirty derived state unavailable
  P-->>A: Complete ACK only after all module obligations committed
```

All transactions needing both locks take subject fence first, partition lease
second; claim cannot hold the latter while waiting for the former. Query holds a
conflicting fence lock through materializing its bounded response, including all
selected subjects for global/course reports in deterministic order. No report
cache in v1. A future cache/rollup must carry epoch/validity generation and cannot
serve a dirty shared result. Deletion completion waits for earlier bounded query
transactions. Already materialized network bytes cannot be recalled; a query
starting after completion cannot expose that lifecycle. Deadline60 s is a
synthetic test target, not permission to falsely ACK a timeout; backlog or missing
module ACK keeps completion pending and is observable.

Deletion does not wait for the live/replay queue: reserve one privacy slot and DB
capacity. Writers with old generation cannot publish, ACK or enqueue downstream
effects after the fence. Quarantine/late/offline/replay obey the same barrier.
Control sequence and deletion ACK are idempotent; differing content for the same
control ID conflicts. Synthetic handlers must disclose which module effects were
covered. No production transport or external side effect is implied here.

## Restore protocol and required runtime checks

At boot/restore keep Collector, Processor and Query unready. Authenticate current
independent journal authority, obtain its high watermark, scan a complete prefix,
apply tombstones and purge deleted contributions, then attach a durable live
control stream without a gap. After attachment request a fresh opening barrier
from the authority and apply the complete continuous prefix through that barrier,
including deletions that arrived during scanning. Atomically record its applied
watermark before opening. Connected but unread queued events are not proof of
safe handoff. Events after this barrier follow the live protocol: Core deletion
stays incomplete until Analytics ACK; local fence commit is the visibility
boundary. Readiness closes when the oldest pending deletion exceeds5 s or trusted
control heartbeat is older than15 s; a deadline failure never becomes success.
These are fixed synthetic targets to verify later, not measured results.
Use the exact policy predicate in privacy_v1.RestoreProof
only after adapter authentication/synchronization; the predicate does not perform
either operation. A stale watermark found in the restored snapshot is insufficient.

| Future owner | Controlled scenario | Required observation |
| --- | --- | --- |
| CORP-427 | Two DB transactions: delete vs old worker commit, lease takeover | Subject-first lock order; old epoch/generation cannot commit or ACK |
| CORP-427 | Query materialization vs delete and global derived result | Earlier bounded read drains; post-completion reads omit deleted contribution, dirty generation never served |
| CORP-427 | Late/replayed/duplicate input, same numeric account ID new lifecycle | Old lifecycle rejected; new opaque UUID allowed, old history not reassigned |
| CORP-65/116 | Restore snapshot predating deletion with fresh journal | No API/worker readiness until complete current prefix applied; subject absent |
| CORP-65/116 | Missing/untrusted/incomplete journal, stream gap or deletion during handoff | Stay closed until the post-attachment opening barrier is fully applied; visible reason, no fake success |

Actual policy checks: tests/privacy/test_policy.py verifies stale snapshot proof,
complete empty authority, missing/untrusted/wrong/incomplete watermark cases. These
are pure contract tests; they do not prove database locks, erasure or recovery.
