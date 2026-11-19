"""Signed synthetic authority records; verified frames are client-created only."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from pymentor_analytics.contracts.v2 import AwareTime, Contract, DeletionFence, Identifier

MAX_ENTRIES = 100
MAX_FRAME_BYTES = 128 * 1024
FRAME_TTL_SECONDS = 3
MAX_SEQUENCE = (1 << 63) - 1


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def deletion_hash(producer: str, control: DeletionFence) -> str:
    return hashlib.sha256(canonical_json({"producer": producer, "control": control.model_dump(mode="json")})).hexdigest()


def deletion_command_bytes(producer: str, nonce: UUID, control: DeletionFence) -> bytes:
    """Detached producer signature binds the verified JWT subject and payload."""
    return b"pymentor:privacy-authority-command:v1\0" + canonical_json(
        {"producer": producer, "nonce": str(nonce), "control": control.model_dump(mode="json")})


class JournalEntry(Contract):
    seq: Annotated[int, Field(ge=1, le=MAX_SEQUENCE)]
    producer: Identifier
    control: DeletionFence
    received_at: AwareTime
    payload_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def hash_matches(self):
        if self.control.epoch > MAX_SEQUENCE:
            raise ValueError("control epoch exceeds storage range")
        if self.payload_hash != deletion_hash(self.producer, self.control):
            raise ValueError("invalid control hash")
        return self


class AuthorityFrame(Contract):
    version: Literal[1]
    authority_id: Identifier
    issuer: Identifier
    audience: Literal["analytics-privacy-journal"]
    kid: Identifier
    kind: Literal["attach", "barrier", "page", "observe"]
    nonce: UUID
    session: UUID
    high_watermark: Annotated[int, Field(ge=0, le=MAX_SEQUENCE)]
    after: Annotated[int, Field(ge=0, le=MAX_SEQUENCE)]
    through: Annotated[int, Field(ge=0, le=MAX_SEQUENCE)] | None
    scanned_through: Annotated[int, Field(ge=0, le=MAX_SEQUENCE)]
    entries: Annotated[tuple[JournalEntry, ...], Field(max_length=MAX_ENTRIES)]
    issued_at: Annotated[int, Field(ge=0)]
    expires_at: Annotated[int, Field(ge=0)]

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("invalid version")
        return value

    @model_validator(mode="after")
    def valid_frame(self):
        if not self.issued_at < self.expires_at <= self.issued_at + FRAME_TTL_SECONDS:
            raise ValueError("invalid frame lifetime")
        if not self.after <= self.scanned_through <= self.high_watermark:
            raise ValueError("invalid frame range")
        if self.kind == "page":
            if self.through is None or not self.scanned_through <= self.through <= self.high_watermark:
                raise ValueError("invalid page upper bound")
            count = self.scanned_through - self.after
            if count > MAX_ENTRIES or len(self.entries) != count or any(
                    item.seq != self.after + index for index, item in enumerate(self.entries, start=1)):
                raise ValueError("non-contiguous page")
        elif self.through is not None or self.entries or self.scanned_through != self.after:
            raise ValueError("invalid non-page frame")
        return self


class AppendReceipt(Contract):
    version: Literal[1]
    kind: Literal["append"]
    authority_id: Identifier
    issuer: Identifier
    audience: Literal["analytics-privacy-journal"]
    kid: Identifier
    nonce: UUID
    control_id: UUID
    producer: Identifier
    payload_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["accepted", "duplicate"]
    seq: Annotated[int, Field(ge=1, le=MAX_SEQUENCE)]
    issued_at: Annotated[int, Field(ge=0)]
    expires_at: Annotated[int, Field(ge=0)]

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("invalid version")
        return value

    @model_validator(mode="after")
    def valid_lifetime(self):
        if not self.issued_at < self.expires_at <= self.issued_at + FRAME_TTL_SECONDS:
            raise ValueError("invalid receipt lifetime")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedFrame:
    """Created after actual signature and request-specific checks, never from wire flags."""
    frame: AuthorityFrame
    request_started_monotonic: float
    verified_at_monotonic: float

    def is_fresh(self, *, now_monotonic: float | None = None) -> bool:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return (self.request_started_monotonic <= self.verified_at_monotonic <= now
                and now - self.request_started_monotonic < FRAME_TTL_SECONDS
                and self.frame.issued_at <= time.time() < self.frame.expires_at)


class AuthorityUnavailable(Exception):
    def __init__(self): super().__init__("authority unavailable")


class AuthorityProtocolError(Exception):
    def __init__(self): super().__init__("invalid authority response")


class AuthorityConflict(Exception):
    def __init__(self): super().__init__("control identity conflicts")
