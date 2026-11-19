"""Separate service credential for future private trusted control transport."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from pymentor_analytics.contracts.v2 import Contract, Identifier


class ProducerClaims(Contract):
    grant_version: Literal[1]
    iss: Identifier
    aud: Literal["analytics-control"]
    sub: Identifier
    iat: Annotated[int, Field(ge=0)]
    nbf: Annotated[int, Field(ge=0)]
    exp: Annotated[int, Field(ge=0)]
    scope: Literal["control:write"]

    @model_validator(mode="after")
    def valid_lifetime(self):
        if not self.iat <= self.nbf < self.exp or self.exp - self.iat > 60:
            raise ValueError("invalid control credential lifetime")
        return self
