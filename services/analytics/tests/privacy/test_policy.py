from dataclasses import replace

import pytest

from pymentor_analytics.contracts import privacy_v1 as policy


def test_old_backup_watermark_cannot_open_restored_service():
    proof = policy.RestoreProof("synthetic-authority", 12, 12, 9, True, True, True, 12)
    assert not proof.permits_opening(expected_authority_id="synthetic-authority")
    assert replace(proof, locally_applied_through=12).permits_opening(
        expected_authority_id="synthetic-authority"
    )


@pytest.mark.parametrize("change", [
    {"complete": False}, {"authenticated_current_observation": False},
    {"authority_id": "other"}, {"scanned_through": 11},
    {"high_watermark": -1}, {"high_watermark": True},
    {"locally_applied_through": True},
    {"complete": "false"}, {"authenticated_current_observation": 1},
    {"stream_attached": False}, {"opening_barrier": 13},
])
def test_missing_incomplete_or_untrusted_journal_stays_closed(change):
    proof = policy.RestoreProof("synthetic-authority", 12, 12, 12, True, True, True, 12)
    assert not replace(proof, **change).permits_opening(expected_authority_id="synthetic-authority")


def test_empty_authority_must_be_explicitly_confirmed_not_assumed():
    proof = policy.RestoreProof("synthetic-authority", 0, 0, 0, True, True, True, 0)
    assert proof.permits_opening(expected_authority_id="synthetic-authority")
    assert not proof.permits_opening(expected_authority_id="")


def test_delete_between_snapshot_scan_and_stream_handoff_must_be_applied():
    proof = policy.RestoreProof("synthetic-authority", 12, 12, 12, True, True, True, 14)
    assert not proof.permits_opening(expected_authority_id="synthetic-authority")
    assert replace(proof, scanned_through=14, locally_applied_through=14).permits_opening(
        expected_authority_id="synthetic-authority"
    )
