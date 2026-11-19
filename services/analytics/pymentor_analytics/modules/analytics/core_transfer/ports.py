from typing import Literal, Protocol
from uuid import UUID

Status = Literal["accepted", "absent", "conflict"]


class TransferStore(Protocol):
    async def accept(self, namespace: str, event: dict, digest: str, *, deadline: float) -> Status: ...
    async def receipt(self, namespace: str, event_id: UUID, digest: str, *, deadline: float) -> Status: ...


class DeletionStatusPort(Protocol):
    """Only a verified journal + completed purge may return accepted."""
    async def status(self, namespace: str, control_id: UUID, subject_ref: UUID,
                     epoch: int, *, deadline: float) -> Literal["accepted", "pending", "absent", "conflict"]: ...
