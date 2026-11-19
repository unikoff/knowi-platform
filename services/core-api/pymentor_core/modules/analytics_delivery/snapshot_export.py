"""Snapshot policy and port-level errors."""
import os

def proof_enabled():
    return os.environ.get("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED") == "1"

class SnapshotDeferred(ValueError):
    """A different cut is still awaiting verified coverage."""

class SnapshotConflict(ValueError):
    """A quarantined proof or fact requires investigation."""
