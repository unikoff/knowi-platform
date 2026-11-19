"""Offline checks for synthetic bootstrap and bounded restore diagnostics."""
from contextlib import nullcontext
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bootstrap_postgres as db
import synthetic_backup as backup


@pytest.mark.parametrize("revision", ["a006", "a010"])
def test_fresh_sentinel_grants_only_migrate(monkeypatch, revision):
    connection = MagicMock()
    monkeypatch.setattr(db, "connect", lambda *_: nullcontext(connection))
    name, _, _ = db.create({}, revision=revision)
    statements = [call.args[0].as_string() if hasattr(call.args[0], "as_string") else call.args[0]
                  for call in connection.execute.call_args_list]
    privileges = [statement for statement in statements if "ON public.analytics_test_sentinel" in statement]
    assert privileges == ["REVOKE ALL ON public.analytics_test_sentinel FROM PUBLIC",
                          f'GRANT SELECT ON public.analytics_test_sentinel TO "{name}_migrate"']


@pytest.mark.parametrize(("message", "category"), [
    (b'ERROR: permission denied for table analytics_test_sentinel secret', 'sentinel permission denied'),
    (b'ERROR: restore guard failed secret', 'restore guard failed'),
    (b'ERROR: duplicate key value secret', 'duplicate key'),
    (b'unknown private text', 'unknown'),
])
def test_psql_failure_classification_never_returns_private_text(message, category):
    assert backup.classify_psql_failure(message) == category
