import hmac
import re
from uuid import UUID

from pymentor_analytics.contracts.core_transfer_v1 import fingerprint, validate_event


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class TransferService:
    def __init__(self, store):
        self.store = store

    async def accept(self, namespace, event, digest, *, deadline):
        # The store must compare an existing ID before rejecting a forged/new ID,
        # so an ID reused with another payload is reported as conflict atomically.
        validate_event(event, check_id=False)
        if not valid_digest(digest) or not hmac.compare_digest(fingerprint(event), digest):
            raise ValueError("invalid fingerprint")
        status = await self.store.accept(namespace, event, digest, deadline=deadline)
        if status not in {"accepted", "absent", "conflict"}:
            raise RuntimeError("invalid transfer outcome")
        return status

    async def receipt(self, namespace, event_id, digest, *, deadline):
        if not valid_digest(digest) or not isinstance(event_id, UUID):
            raise ValueError("invalid receipt")
        status = await self.store.receipt(namespace, event_id, digest, deadline=deadline)
        if status not in {"accepted", "absent", "conflict"}:
            raise RuntimeError("invalid transfer outcome")
        return status
