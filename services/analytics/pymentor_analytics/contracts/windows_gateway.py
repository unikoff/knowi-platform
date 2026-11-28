"""Private Core proof carried beside the signed browser grant."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .v2 import Contract, Identifier
from .windows_v1 import Generation, Hash, MAX_KEYS


class WindowForwardClaims(Contract):
    iss: Identifier
    aud: Literal["analytics-windows-gateway"]
    sub: UUID
    iat: Generation
    nbf: Generation
    exp: Generation
    scope: Literal["windows:forward"]
    body_sha256: Hash
    grant_sha256: Hash
    rejected: Annotated[tuple[Literal["course_forbidden", "tool_forbidden"] | None, ...],
                        Field(min_length=1, max_length=MAX_KEYS)]

    @model_validator(mode="after")
    def lifetime(self):
        if not self.iat <= self.nbf < self.exp <= self.iat + 15:
            raise ValueError("invalid gateway proof lifetime")
        return self
