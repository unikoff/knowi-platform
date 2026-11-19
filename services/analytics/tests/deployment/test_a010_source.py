"""Offline release preparation checks; no container or database access."""
import json
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

SERVICE = Path(__file__).resolve().parents[2]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE / "tools"))
import bootstrap_postgres as db
import bootstrap_runtime as runtime
import check_profile
import synthetic_backup as backup

HELM = SERVICE / ".local/helm-sourcecheck/helm.exe"
CHART = ROOT / "infra/helm/analytics"


def test_roles_and_pinned_history_are_revision_specific():
    assert db.roles_for_revision("a006") == db.ROLES
    assert db.roles_for_revision("a010") == db.ROLES + ("transfer_status",)
    assert db.roles_for_revision("a011") == db.ROLES + ("transfer_status",)
    with pytest.raises(ValueError):
        db.roles_for_revision("head")
    current = runtime.schema_history()
    a006 = runtime.schema_history("a006")
    assert current[0] == "a011"
    assert current[1] == ("a011", "a010", "a009", "a008", "a007", "a006", "a005", "a004", "a003", "a002", "a001")
    assert runtime.schema_history("a010")[2] == "834da297a72e0c6d7e1738aa76f6b07dcf27868e54fe132c882228f970b6700a"
    assert a006[0] == "a006" and a006[1][0] == "a006"
    assert a006[2] == "2c19510585fe3df8e95a9d29ff409301ff6b29a5fb96b622446594acbc7fe370"
    for name in ("a007_core_transfer.py", "a008_catalog.py", "a009_core_snapshot_proofs.py", "a010_activity_projection.py", "a011_activity_day_projection.py"):
        assert (SERVICE / "migrations/versions" / name).is_file()
        assert not (SERVICE / "plans" / name).exists()
    with pytest.raises(ValueError):
        runtime.schema_history("a999")


def test_transfer_connection_matrix():
    profile = json.loads((SERVICE / "bench/profile-v1.json").read_text())
    assert check_profile.calculate(profile, core_transfer=True)["connections"] == {
        "1/1/1": 13, "2/1/1": 16, "1/2/1": 15, "1/1/2": 15, "2/2/2": 20}


def test_transfer_role_intent_precedes_ddl_and_failure_is_left_pending(monkeypatch):
    name = "analytics_test_" + "a" * 32
    record = {"state": "ready", "database": name, "urls": {}}
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr(db, "connect", lambda *_: nullcontext(connection))
    saved = []
    monkeypatch.setattr(runtime, "write_record", lambda value: saved.append(deepcopy(value)))
    runtime.provision_transfer_status({}, record)
    assert [x["state"] for x in saved] == ["role_pending", "ready"]
    assert "transfer_status" in saved[0]["urls"] and connection.execute.call_count == 3

    record = {"state": "ready", "database": name, "urls": {}}
    saved.clear()
    connection.execute.side_effect = [MagicMock(fetchone=lambda: None), RuntimeError("unknown")]
    with pytest.raises(RuntimeError):
        runtime.provision_transfer_status({}, record)
    assert record["state"] == "role_pending" and [x["state"] for x in saved] == ["role_pending"]
    with pytest.raises(ValueError):
        runtime.provision_transfer_status({}, record)


def test_a010_restore_only_clears_exact_known_fresh_seeds(monkeypatch):
    rows = [(True, 60, 600, 10000, 536870912, 80, 90, 0, 0, False)]
    projection = [(True, None, "scanning", None, None)]
    sessions = [0]
    privilege = [False]
    def execute(query, *_args):
        text = str(query)
        if "pg_catalog.pg_tables" in text:
            result = [("admission_budget",), ("activity_projection_backfill",), ("activity_facts",)]
        elif "pg_stat_activity" in text:
            result = [(sessions[0],)]
        elif "pg_read_all_stats" in text:
            result = [(True,)]
        elif "has_database_privilege" in text:
            result = [(privilege[0],)]
        elif "count(*)" in text:
            result = [(0 if "activity_facts" in text else 1,)]
        elif "SELECT singleton,subject_per_minute" in text:
            result = rows
        elif "SELECT singleton,run_id,phase" in text:
            result = projection
        else:
            result = []
        response = MagicMock()
        response.fetchall.return_value = result
        response.fetchone.return_value = result[0] if result else None
        return response
    conn = MagicMock()
    conn.execute.side_effect = execute
    conn.transaction.return_value = nullcontext()
    monkeypatch.setattr(backup.db, "connect", lambda *_: nullcontext(conn))
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    record = {"kind": "restore", "state": "restore_pending", "database": "synthetic", "history": {"head": "a010"}}
    backup.clear_fresh_defaults({}, record)
    calls = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert sum("DELETE FROM analytics." in call for call in calls) == 2
    assert sum("REVOKE CONNECT ON DATABASE" in call for call in calls) == 4
    assert max(i for i, call in enumerate(calls) if "REVOKE CONNECT" in call) < min(i for i, call in enumerate(calls) if "DELETE FROM analytics." in call)
    projection[:] = [(True, None, "complete", None, None)]
    conn.reset_mock()
    with pytest.raises(ValueError, match="projection seed"):
        backup.clear_fresh_defaults({}, record)
    assert not any("DELETE FROM analytics." in str(call.args[0]) for call in conn.execute.call_args_list)
    projection[:] = [(True, None, "scanning", None, None)]
    sessions[:] = [1]
    conn.reset_mock()
    with pytest.raises(ValueError, match="other or unobservable sessions"):
        backup.clear_fresh_defaults({}, record)
    assert not any("REVOKE CONNECT" in str(call.args[0]) or "DELETE FROM analytics." in str(call.args[0]) for call in conn.execute.call_args_list)
    sessions[:] = [0]
    privilege[:] = [True]
    conn.reset_mock()
    with pytest.raises(ValueError, match="quarantine not established"):
        backup.clear_fresh_defaults({}, record)
    assert not any("DELETE FROM analytics." in str(call.args[0]) for call in conn.execute.call_args_list)


@pytest.mark.skipif(not HELM.is_file(), reason="verified local Helm not installed")
def test_feature_render_and_deny_by_default():
    base = [str(HELM), "template", "analytics", str(CHART), "--namespace", "knowi-analytics"]
    defaults = subprocess.run(base, capture_output=True, text=True, check=True).stdout
    assert "CORE_TRANSFER_STATUS_DATABASE_URL" not in defaults
    assert "analytics-core-ingress" not in defaults
    flags = ["--set", "coreTransfer.enabled=true", "--set", "coreTransfer.proofEnabled=true",
             "--set", "catalog.enabled=true", "--set", "activityProjection.enabled=true",
             "--set", "network.coreIngress.enabled=true", "--set-string", "migration.revision=a010",
             "--set-string", "trustManifest.configMapName=analytics-trust",
             "--set-string", "privacyAuthority.secretName=analytics-authority",
             "--set-string", "coreTransfer.statusSecretName=analytics-transfer-status",
             "--set-string", "coreTransfer.namespacesConfigMapName=analytics-namespaces",
             "--set-string", "coreTransfer.queryNamespace=core-test",
             "--set-string", "catalog.namespace=core-test",
             "--set-string", "activityProjection.runId=11111111-1111-4111-8111-111111111111",
             "--set-string", "network.coreIngress.namespace=core",
             "--set-string", "network.coreIngress.podLabels.app=core-api"]
    out = subprocess.run(base + flags, capture_output=True, text=True, check=True).stdout
    docs = out.split("\n---\n")
    deployments = {role: next(x for x in docs if "kind: Deployment" in x and f"name: analytics-analytics-{role}" in x)
                   for role in ("collector", "processor", "query")}
    assert "ANALYTICS_COLLECTOR_CORE_TRANSFER_STATUS_DATABASE_URL" in deployments["collector"]
    assert all("ANALYTICS_COLLECTOR_CORE_TRANSFER_STATUS_DATABASE_URL" not in deployments[x] for x in ("processor", "query"))
    run = 'ANALYTICS_ACTIVITY_PROJECTION_RUN_ID, value: "11111111-1111-4111-8111-111111111111"'
    assert all(run in deployments[x] for x in ("processor", "query"))
    policy = next(x for x in docs if "name: analytics-analytics-core-ingress" in x)
    assert 'kubernetes.io/metadata.name: "core"' in policy
    assert "app: core-api" in policy and "podSelector:" in policy
    bad = subprocess.run(base + flags[:-2], capture_output=True, text=True)
    assert bad.returncode != 0
