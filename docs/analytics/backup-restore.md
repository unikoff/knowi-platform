# CORP-65: synthetic encrypted backup, partial evidence

This tool is restricted to disposable fixture databases created by
`synthetic_backup.py`; it refuses the retained Analytics runtime database. The
only database server it accepts is the named synthetic Analytics PostgreSQL
container, whose project/service labels, running state, loopback 57392 binding
and PostgreSQL 16.16 client versions are checked. The tool never targets Core,
overwrites an existing DB, drops a DB automatically, or uploads anything.

The data-only PostgreSQL custom archive contains the `analytics` schema's data,
not public sentinel/Alembic history, database roles or grants. The target is a
new UUID database with new roles and sentinel, migrated to the exact captured
revision. Before restore it verifies that every Analytics table is empty apart
from the exact known admission-budget seed, and deletes only that seed. Restore
uses `pg_restore --data-only --single-transaction --exit-on-error --no-owner
--no-acl`. This deletion and restore are separate transactions; any failure or
unknown outcome leaves the target and operation metadata for reconciliation,
never automatic retry or deletion. `PGPASSWORD` is passed only through the
process environment, not command arguments or output.

AES-256-GCM uses a fresh random nonce and bounded streaming chunks. The header,
including source identity, timestamp, revision, complete migration-history hash,
runtime-code fingerprint, archive SHA-256 and size, is authenticated as AAD;
the header itself is not encrypted. The dump is encrypted. `verify` authenticates
the complete tag and checks SHA-256 before returning; `restore` performs that
verification before creating the target or executing any restore SQL. Wrong
keys, tampered headers/ciphertext/tags and truncated files fail closed. The
maximum dump size is 512 MiB; streaming avoids loading the full archive in RAM.

Key files are separate from encrypted artifacts under ignored
`.local/synthetic-backup/keys` and `artifacts`; their operation metadata is under
`operations`. Keep the key and recovery instructions in a separately protected
location before any real recovery exercise. Private plaintext dump staging exists
briefly under `staging`, and may remain after interruption or failed restore;
restrict directory ACLs and reconcile it manually. File deletion does not prove
secure erasure on SSD or snapshots. Windows mode bits alone do not enforce private
ACLs. Never print or include a key, signing credential or connection URL in a
report. No rotation or scheduled/off-device delivery is implemented: the tool
never prunes any copy, so all existing verified and unverified copies are kept.
This preserves earlier good copies but does not establish four server-side
restore-verified backups available when this computer is offline.

## Future integration with the existing backup operation

For a deployed Analytics database, reuse the established
[knowi-backup operational policy and receiver](../../infra/operations/knowi_backup/README.md)
after independently configuring and verifying Analytics-specific node, workload,
database and storage anchors. The existing scripts and anchors are for Core;
their presence does not mean they already protect Analytics. Do not disable Core
identity checks or point the existing Core configuration at Analytics.

The intended operational model uses the existing public-recipient CMS
AES-256-GCM/RSA-OAEP encryption and receiver/pull path, with a separate
Analytics configuration and verified anchors. Only the public recipient
certificate belongs on the server; the private key remains on the user's
computer. Reuse the established server schedule, Monday and Thursday at 03:00
Moscow, and retain the latest four server-side restore-verified copies regardless
of whether the computer is online. A failed new verification preserves the prior
four; space for a fifth is required during creation. The existing computer-side
pull is a separate step and depends on its own online/session conditions.

This is a future integration, not an applied change or verified Analytics backup
deployment. It requires checking that the receiver and access boundaries support
the separate Analytics configuration before use. The symmetric local fixture
codec above is test tooling, not a production replacement for CMS, the existing
receiver or its retention policy. Do not introduce another scheduler or cloud
storage to replace that known operational path.

From `services/analytics`, after explicitly preparing the synthetic PostgreSQL
environment and confirming resource capacity, the operator supplies unique UUIDs:

```powershell
.\.venv\Scripts\python.exe -B tools/synthetic_backup.py key-create --key <key-uuid>
.\.venv\Scripts\python.exe -B tools/synthetic_backup.py fixture-create --fixture <fixture-uuid>
# Populate only that fixture with synthetic data, keeping it quiescent for backup.
.\.venv\Scripts\python.exe -B tools/synthetic_backup.py backup --source <fixture-uuid> --backup <backup-uuid> --key <key-uuid>
.\.venv\Scripts\python.exe -B tools/synthetic_backup.py verify --backup <backup-uuid> --key <key-uuid>
.\.venv\Scripts\python.exe -B tools/synthetic_backup.py restore --backup <backup-uuid> --key <key-uuid> --restore <new-restore-uuid>
.\.venv\Scripts\python.exe -B tools/synthetic_backup.py parity --restore <new-restore-uuid>
```

Replace placeholders without angle brackets. Backup compares deterministic
bounded row digests before and after the dump and rejects changes; this is a
synthetic quiescent-fixture check, not a substitute for a concurrent production
snapshot protocol. `parity` compares all restored Analytics tables against the
authenticated fixture digest even when the source DB is gone. The tool records
three separate facts: `crypto_verified` means the encrypted file authenticated;
`data_restored` means pg_restore returned success; `parity_verified` means the
bounded fixture table comparison passed. None means `privacy_opened`.

The independent deletion authority/journal is not in this app-DB backup. Local
privacy state and receipts in the dump may be stale and cannot establish a
current external watermark. Never open restored Collector, Processor or Query
until an independent authority's authenticated prefix, attached stream and fresh
opening barrier have been verified and applied. The backup tool neither starts
those processes nor opens this barrier.

## Observed evidence and remaining gap

On 16.11.2026 an explicitly enabled test created a fresh isolated synthetic DB
with one fact on the existing Analytics PostgreSQL container. It encrypted and
authenticated its custom dump, deleted only that fixture using its own sentinel
and owner, restored into a second newly migrated DB and compared the full
Analytics table digest with the authenticated fixture digest. Storage restore to
parity took **2.005 s** and the one seeded fact was restored. This demonstrates
fixture parity; it does not measure data loss for changes after the snapshot.
This is not a general RPO or RTO measurement and
does not include external deletion-barrier recovery. Both test databases were
removed after their authorized checks; encrypted artifact and separate key were
retained. The retained runtime DB and its volume were not modified.

In a separate quiescent synthetic fixture, one fact was committed after the
backup and before simulated loss. Exactly that one late fact was absent after
restore; snapshot age at simulated loss was **0.029 s**, the gap between the
latest committed and latest restored fixture facts was **1.180 s**, and storage
restore to parity took **1.782 s**. Both fixture DBs were removed by their own
sentinel/owner checks after their respective steps. These are observations from
one controlled case, not an operational RPO target or a scheduler guarantee.

An additional experiment deleted its own source fixture after authenticating the
copy, then restored it into a distinct nonce-identified PostgreSQL node on a
separate internal Docker network with tmpfs storage. The destination could not
reach the source node's inspected IP, restored-table parity passed, and the
temporary node and network were removed after proof. Post-node-ready storage
restore to parity took **772.290 s**, including repeated Docker identity checks;
`privacy_opened` remained false. In that run the test driver still inspected the
source container's network metadata before restore, and the original host/node
remained running. It therefore does not prove recovery when that node is absent.

A second isolated run recorded the source IP before simulated loss, removed its
own source fixture, blocked the source DB connector and source container name
during restore, and verified that the destination could not connect to that IP.
It restored the snapshot into a new node, verified table parity and removed the
nonce-owned node and network. Post-node-ready storage restore to parity took
**80.381 s**; `privacy_opened` remained false. In the executed test, dynamic
source-reference blocking began after target-node creation; that earlier interval
contained only local metadata and target Docker operations by source inspection.
The test driver now starts blocking before node creation, but that exact version
has not been run. Neither test powered down the original host or established
off-device key/artifact availability.

Recovery without the original physical host, independent/off-device artifact and key
availability, four verified copies, scheduling and production-scale load remain
**UNVERIFIED**. These earlier results alone did not verify privacy opening.

## Combined local proof, 17.11.2026

`tests/backup_postgres/test_isolated_privacy.py` uses a distinct temporary
PostgreSQL source with nonce-owned container, network, database, roles and
sentinel. It takes a bounded custom `pg_dump`, encrypts and authenticates it,
then commits a signed deletion to a separately stored synthetic authority
journal. It confirms that the journal can be reopened and stops and identifies
the source container before creating the target. The target is on a different
dedicated bridge; source connector and source-container references are blocked
throughout target creation and restore. After full snapshot parity it checks
that Collector, Processor and Query remain unavailable before a current signed
authority observation and applied prefix, and that the deleted subject is gone
while a second synthetic subject survives. Cleanup of the exact owned nodes and
networks occurs only after successful proof and identity checks; failure leaves
operation metadata for reconciliation, never automatic retry.

The pre-loss authentication was a preflight; the target restore authenticated
the encrypted copy again after source loss. The operation record distinguishes
storage-only restore time from local recovery time measured from the confirmed
source stop through the positive current-barrier and surviving-subject checks.
It also records snapshot age and the observed fixture fact gap. A run with no
post-snapshot fact commits cannot establish an operational RPO; the separate
late-write exercise above observes a loss of one such commit.

The explicitly enabled test passed once in **39.90 s**. It created an encrypted
custom PostgreSQL dump containing two synthetic subjects and committed a signed
deletion to an independent SQLite journal after the snapshot. It reopened the
journal, stopped and verified the source container before target creation, and
restored into the separately owned target. On the restored snapshot Collector,
Processor and Query were unavailable; an unavailable authority and an incorrect
signing key also remained closed. A current signed authority observation had
high watermark 1 while the restored applied prefix was 0. After replay, the
applied prefix was 1, the deletion fence existed and that subject's fact was
absent, the other subject's fact remained, and the in-process apps reported
ready. The source-container and source-connector guard covered target creation
and restore.

The source operation `41aaf7ab-e7c5-4f59-a86b-05a8cbf43057` recorded
`deleted_after_proof`. The target operation
`5425b01c-d86e-4183-b0da-ee482edc7b79` recorded authenticated copy, data
restored, snapshot parity, `privacy_opened=true` and target cleanup. Read-only
checks confirmed that both test containers and networks were absent, with no
test probe or migrator container left behind. The encrypted artifact and key
remain separate, and the journal was reopened again with one committed entry.

Measured local recovery from confirmed source stop through the positive
current-barrier and surviving-subject checks was **27.875 s**. Storage restore
from the ready target through snapshot parity was **9.996 s**. Snapshot age at
confirmed loss was **0.411 s**; the latest committed fixture-fact gap was zero,
with **zero post-snapshot fact commits** in this case. These are single fixture
observations, not operational RTO or RPO guarantees; no schedule is established.

The nodes run sequentially, each capped at 256 MiB, with a transient target
migration or image probe capped at 128 MiB. The local test also enforces fresh
available memory of at least 1536 MiB, requires a free loopback port 57422 and
an immutable locally available migrator image; admission must account for all
other workloads and reserve before a run. The dedicated fixture bridge is
host-reachable, has no other attached container, and is distinct from Core and
the existing Analytics networks. Test code checks node, network, loopback
binding, database sentinel and owner before constructing a test-only settings
copy for the exact port. The service's normal settings validation continues to
reject loopback 57422 and is not tested by this adaptation. The app and privacy
runtime run in-process against real fixture PostgreSQL, not in K3s.

The encrypted artifact, separate key and SQLite authority journal are on the
same physical computer as the fixture
nodes. This run establishes only the stated local synthetic case,
not independent off-host storage, four verified server copies, production
schedule, or operation under a physical host loss.
