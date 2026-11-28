# CORP-603 · PostgreSQL storage tests

These tests create a separate synthetic database per pytest test on the designated loopback Analytics PostgreSQL cluster. See [the local runbook](../../../../docs/analytics/run-local.md) for PostgreSQL startup and [CORP-603](../../../../docs/analytics/CORP-603-storage.md) for acceptance.

From `services/analytics`, use Python 3.12 with `requirements-dev.txt` installed. The fixture reads the ignored `.local/postgres-connection.json` unless `ANALYTICS_STUDENT_WINDOWS_PG_CONFIG` points at the credentials already provisioned for the same designated synthetic cluster. Keep the file and role URLs private.

```powershell
$env:ANALYTICS_RUN_STUDENT_WINDOWS_SYNTHETIC = '1'
python -B -m pytest tests/student_windows/test_storage_pg.py -q -rs -s -p no:cacheprovider -k test_a014_privacy_baseline
```

The fixture verifies database owner and sentinel, then explicitly migrates to `a014`. The printed run UUID, database name, sentinel SHA-256, and server version identify the run. Cleanup uses the original sentinel and owner check and verifies removal of the database and all six per-target roles. Suite invocations may use the same server with their own database, role credentials, and sentinel. For an interrupted run, record the printed target, check the owner and the SHA-256 of the sole sentinel row against the original fixture output, and inspect Alembic revision, cursor, fence and outstanding sessions before resuming. Provision a fresh database if ownership cannot be verified.

The baseline writes two real v2 facts through the Collector port. The Query router, Query database role and v2 calculation observe 10 seconds of theory for the selected student. The test supplies an in-process grant and readiness seam. The dedicated Privacy role purges one lifecycle, repeats the exact control and preserves the independent lifecycle. A separate connection verifies the cursor, receipt, fence and remaining fact. No v3 table exists at this point. The snapshot timestamp is observed but excluded from the business comparison.

## Prepared checks for 603.B and 603.C

| Scenario | Fixture and synchronization | Independent observation |
| --- | --- | --- |
| 603-А · `test_a014_upgrade_compatible` | Reuse the 10-second v2 fact and Query route with v3 disabled. Apply the expansion twice using the explicit migrator. | Compare stable report fields and persisted fact. Check one copy of each new object and zero v3 rows. |
| Early `test_store_privacy_smoke` | Use the agreed 602.A seq 4 snapshot at 480000 ms, a separate Collector role and the ordinary Privacy role. | Inspect committed revision from another session, business/control row removal, durable fence, denied old replay and denied direct Collector repair write. |
| 603-Б · `test_concurrent_writes_lost_ack` | Seed seq 1 at 120000 ms. Hold the transaction that takes the subject lock first until the second connection attempts its write. Run both orders with seq 2 at 240000 ms and seq 3 at 480000 ms, then discard the committed seq 3 reply. Use explicit synchronization and bounded waits. | Read one seq 3 row with its expected hash and 480000 ms. Expect R+2 when seq 2 reaches commit first and R+1 when seq 3 commits first. Retry the same body and check duplicate at the same revision. |
| 603-В · `test_write_and_repair_race_with_purge` | Seed business/control state for one lifecycle and one unaffected row for another. Use separate connections and the subject lock as the barrier to exercise both commit orders for write vs purge and repair vs purge. Pause after the first purge page commits its fence, then replay the same control. | Verify cursor advances only after complete removal, no owned rows remain, the other lifecycle retains one row, fence remains, late replay and repair cannot recreate deleted rows. |
| 603-Г · `test_changed_key_commit_and_rollback` | Use one 30000 ms v2 fact from 2026-11-27 23:59:50 to 2026-11-28 00:00:20 MSK. Raise inside the fact transaction before commit once, then repeat with a commit and replay the exact same fact. | Compare using the PostgreSQL transaction boundary. Rollback leaves zero keys. Commit marks 27/96 and 28/1 with the agreed generation. Replay leaves generation and v2 business time unchanged. |
| 603-Д · `test_restore_uses_current_deletion_journal` | Make a custom-format archive from a source with one 480000 ms window, excluding its sentinel. Append the delete after taking the archive. Restore into a fresh target with its own sentinel. Use separate source and target DBs and current independent deletion journal. | A new process stays closed before applying the journal. Inspect restored target after catch-up for applied cursor, fence and zero deleted rows. Reject the old snapshot. Allow 30000 ms for a new lifecycle. |

Once the corresponding tests exist, select the early cases with `-k 'test_a014_upgrade_compatible or test_store_privacy_smoke'`, or run the full module without `-k`. Use `pg_dump --format=custom --no-owner --no-acl --exclude-table=public.analytics_test_sentinel` and `pg_restore --exit-on-error --no-owner --no-acl` on the verified archive target. Follow the existing [a014 restore test](../activity_day_projection/test_a014_full_restore_pg.py) for target ownership checks and cleanup. A skipped or uncollected case leaves its acceptance scenario open.


## Full PostgreSQL and archive check

Use the same synthetic configuration and PostgreSQL opt-in as above. Set the archive opt-in and verify the exact container before running the whole module.

```powershell
$container = docker inspect --format '{{.Id}}' knowi-analytics-01a1df75-analytics-postgres-1
if ($LASTEXITCODE -ne 0 -or $container -notmatch '^[0-9a-f]{64}$') { throw 'No exact synthetic container identity' }
$env:ANALYTICS_SYNTHETIC_PG_CONTAINER_ID = $container
$env:ANALYTICS_RUN_STUDENT_WINDOWS_ARCHIVE = '1'
python -B -m pytest tests/student_windows/test_storage_pg.py -q -rs -s -p no:cacheprovider
```

The archive case uses `pg_dump --format=custom --data-only --no-owner --no-acl` on the two window tables and three Privacy tables only. It runs `pg_restore --exit-on-error --single-transaction --data-only --no-owner --no-acl` into a different sentinel DB pre-migrated to a015. This preserves the target's role grants and its own sentinel. The bootstrap role is used for copying archive data. Ordinary Collector and Privacy roles exercise access on the restored database. The independently stored SQLite journal is kept outside the archive, and the test verifies its signed frames through the synthetic authority HTTP app on ASGI. A restored Collector must stay closed until the latest authority prefix has been applied. Both DBs and their dedicated roles are removed with sentinel checks at the end.
