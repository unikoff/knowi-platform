# Authenticated privacy runtime (CORP-427)

The `privacy` command is a separate technical worker in the Analytics image,
using only its own PostgreSQL role. Collector, Processor and Query observe the
independent authority with read-only credentials. No Core connection or real
Tracker is enabled. The three responsibility zones remain Collector, Analytics
and PostgreSQL; privacy synchronization is a processing responsibility.

Configure `ANALYTICS_PRIVACY_DATABASE_URL` for the worker and each existing
role's own database URL. All four use one `ANALYTICS_PRIVACY_AUTHORITY_JSON`
**Secret**, containing the private fixture URL, pinned authority identity and
response keys, read-only `reader_secret`, and explicit `fixture_mode: true`.
Do not place it in the public grant trust manifest, a ConfigMap, probe header,
logs or Git. The response signer must differ from both JWT signer domains.
Producer JWT and detached command-signing keys are not supplied to readers.
See [authority](privacy-authority.md) for explicit fixture setup and CLI.

Run `python -m pymentor_analytics.app.cli privacy --port <private-health-port>`.
It runs one bounded deletion chunk per poll (at most one journal entry and100
rows per physical table), with a dedicated pool. No command advances its ACK
until every installed deletion obligation is empty. Missing configuration leaves
`/live`200 and `/ready`503 and starts no synchronization. Existing application
Compose/Helm does not yet deploy this optional worker or authority fixture; that
wiring remains part of the incomplete infrastructure gate.

Each process starts closed. It authenticates a fresh attach, obtains a new
opening barrier after attachment, and waits until the database has applied that
continuous prefix. An unverified source cannot even bind an empty database's
authority identity. Persisted cursor/old ready state cannot open a new process.
Every poll obtains a new signed current HWM. The gate requires an equal applied
cursor, a fulfilled opening barrier, and a response fresh under both the signed
wall-clock expiry and the3-second monotonic budget. It polls approximately once
per second and closes on any pending deletion, source failure, gap, rollback or
stale observation. This is stricter than waiting for a5-second pending age or
15-second heartbeat. These bounds are protocol settings, not a measured SLO.

Collector, Processor and Query check the gate both before and at the end of each
runtime database transaction. Privacy uses an independent unguarded control pool
so that a closed gate cannot prevent the deletion that reopens it. State reads
take no row lock; this avoids state→subject versus subject→state deadlock. Actual
read/write/delete linearization still uses the common subject locks and epochs.
A read materialized under the shared actor/target locks precedes delete's ACK;
already materialized bytes cannot be recalled. New reads after ACK cannot return
the deleted contribution. No new cache or global rollup exists in this phase.

An unknown deletion commit closes the controller's local barrier. Its next tick
reads the committed cursor and authenticates a new attach/barrier; it never
infers ACK or repeats a state reset. Subject tombstones and minimal control
receipts remain durable without TTL. Future projections must add their deletion
obligation before becoming readable.

## Actual verification

Root gate/configuration tests use fake ports to verify handoff, pending/stale
closure, unknown outcome, transaction rechecks and cancellation. These tests do
not prove signatures or PostgreSQL transactions.

`python -B tools/test_privacy_integration.py` uses a real bounded fixture HTTP
listener, independent SQLite journal, Ed25519 signatures and fresh PostgreSQL
databases with sentinel/owner cleanup. It exercises signed user HTTP intake,
control projection processing, scoped Query gate, signed deletion, refusal of
late collection, and a logical old-snapshot transplant into a second clean DB.
The restored reader remains closed until the **current independent** journal is
applied; unavailable or untrusted sources cannot open it. Separate storage tests
cover worker/query/replay races, bounded partial purge and restart.

The logical snapshot scenario is not an encrypted backup/pg_dump restore,
off-node recovery, HA, performance test or CORP-65/109 acceptance. K3s, common
host failure, real Core integration and operational backup delivery are unrun.
The fixture authority must not be rolled back together with application data;
rolling back both sources without a current external anchor is not detectable
from those stale copies alone. All input identities are synthetic.
