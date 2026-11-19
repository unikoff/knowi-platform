"""Analytics capture policy and the SQL write-gate port."""
import os
from pymentor_core.shared.bindings import get_bindings

def enabled() -> bool:
    return os.environ.get("PYMENTOR_ANALYTICS_DELIVERY_ENABLED") == "1"

def task_enabled() -> bool:
    return enabled() and os.environ.get("PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED") == "1"

def write_mode() -> str:
    mode = os.environ.get("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    if mode not in {"mirror", "outbox_only"}:
        raise ValueError("Invalid Analytics legacy write mode")
    if mode == "outbox_only" and (not enabled() or os.environ.get("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "off") != "analytics"):
        raise ValueError("Outbox-only activity requires Analytics capture and reads")
    return mode

def require_write_gate(db, expected: str) -> None:
    return get_bindings().analytics_delivery.require_write_gate(db, expected)
