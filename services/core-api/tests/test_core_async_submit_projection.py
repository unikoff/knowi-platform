"""ASGI consumer checks for the shared submit projection (runner/DB stubbed)."""
import pytest
from regression.test_submit_response_projection import api_harness, _submit


@pytest.mark.parametrize("source", ["chapter_01/block_01/test.md", "chapter_02/block_01/test.md", "chapter_03/block_01/test.md"])
@pytest.mark.parametrize("success", [False, True])
def test_submit_hides_judge_values_across_chapters(api_harness, monkeypatch, source, success):
    # This fixture intentionally stubs DB/identity; the production authority stays mandatory.
    from pymentor_core.modules.identity.api import sessions
    monkeypatch.setattr(sessions, "_local_async_identity", lambda request: None)
    api_harness.state.source_file = source
    secret = "PRIVATE_JUDGE_SENTINEL"
    api_harness.state.result = {
        "ok": success, "score": 100 if success else 0,
        "tests": [{"name": secret, "passed": success, "expected": secret, "actual": secret, "error": secret}],
        "stdout": secret, "stderr": secret, "traceback": secret,
    }
    response = _submit(api_harness)
    assert response.status_code == 200
    assert secret not in response.text
    assert response.json()["ok"] is success
    assert api_harness.state.events == (["progress"] if success else [])
