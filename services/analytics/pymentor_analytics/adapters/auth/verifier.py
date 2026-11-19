"""Strict local Ed25519 verification. Does not contact Core or a remote key URL."""
from __future__ import annotations

import base64
import json
import re
from types import MappingProxyType
from typing import Mapping

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import TypeAdapter, ValidationError

from pymentor_analytics.contracts.v2 import GrantClaims, Identifier, JwtHeader
from pymentor_analytics.contracts.producer_v1 import ProducerClaims
from pymentor_analytics.contracts.privacy_v1 import COLLECTION_GRANT_TTL_SECONDS, REPORT_GRANT_TTL_SECONDS
from pymentor_analytics.contracts.auth_v1 import AuthenticationError, MAX_TOKEN_BYTES, VerifiedGrant, VerifiedProducer

MAX_HEADER_BYTES = 1024
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("invalid JSON constant")


def _decode_segment(value: str) -> bytes:
    if not _B64URL.fullmatch(value):
        raise ValueError("invalid base64url")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise ValueError("noncanonical base64url")
    return decoded


def _parts(token: str) -> tuple[JwtHeader, bytes]:
    if not isinstance(token, str) or not token.isascii() or len(token) > MAX_TOKEN_BYTES:
        raise ValueError("invalid token")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid compact JWT")
    header_bytes, payload_bytes = _decode_segment(parts[0]), _decode_segment(parts[1])
    signature = _decode_segment(parts[2])
    if len(header_bytes) > MAX_HEADER_BYTES or len(signature) != 64:
        raise ValueError("invalid header or signature")
    # Inspect untrusted JSON only for structural rejection and selecting a local key.
    for raw in (header_bytes, payload_bytes):
        if not isinstance(json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant), dict):
            raise ValueError("JWT object required")
    return JwtHeader.model_validate_json(header_bytes), payload_bytes


class _LocalVerifier:
    def __init__(self, *, issuer: str, audience: str, public_keys: Mapping[str, bytes]):
        try:
            TypeAdapter(Identifier).validate_python(issuer, strict=True)
            if not public_keys or len(public_keys) > 32:
                raise ValueError("invalid keyring")
            keys = {}
            for kid, pem in public_keys.items():
                TypeAdapter(Identifier).validate_python(kid, strict=True)
                if not isinstance(pem, bytes) or not 1 <= len(pem) <= 4096:
                    raise ValueError("invalid public key")
                key = serialization.load_pem_public_key(pem)
                if not isinstance(key, Ed25519PublicKey):
                    raise ValueError("Ed25519 key required")
                keys[kid] = key
            self._keys = MappingProxyType(keys)
            self._issuer = issuer
            self._audience = audience
        except (ValueError, TypeError, ValidationError, UnsupportedAlgorithm):
            raise ValueError("invalid verifier configuration") from None

    def _verify(self, token: str, *, audience: str | None = None) -> bytes:
        try:
            header, payload = _parts(token)
            key = self._keys.get(header.kid)
            if key is None:
                raise ValueError("unknown key")
            jwt.decode(token, key=key, algorithms=["EdDSA"], issuer=self._issuer,
                       audience=audience or self._audience, leeway=0,
                       options={"require": ["iss", "aud", "sub", "exp", "iat", "nbf"],
                                "verify_signature": True, "verify_exp": True,
                                "verify_iat": True, "verify_nbf": True,
                                "verify_iss": True, "verify_aud": True, "strict_aud": True})
            return payload
        except (ValueError, TypeError, OverflowError, UnicodeError, jwt.PyJWTError, ValidationError, RecursionError):
            raise AuthenticationError() from None


class GrantVerifier(_LocalVerifier):
    def __init__(self, *, issuer: str, public_keys: Mapping[str, bytes]):
        super().__init__(issuer=issuer, audience="analytics", public_keys=public_keys)

    def verify(self, token: str) -> VerifiedGrant:
        try:
            claims = GrantClaims.model_validate_json(self._verify(token))
            max_ttl = COLLECTION_GRANT_TTL_SECONDS if claims.scope == "collect" else REPORT_GRANT_TTL_SECONDS
            if claims.exp - claims.iat > max_ttl:
                raise ValueError("grant lifetime exceeded")
            course = claims.course_completion_roster_v1
            return VerifiedGrant(claims.sub, claims.role, claims.scope, tuple(claims.course_ids),
                                 tuple(claims.subject_refs), claims.global_read, claims.exp,
                                 (tuple((member.subject_ref, member.revision, member.student)
                                  for member in claims.legacy_admin_roster_v1.members)
                                  if claims.legacy_admin_roster_v1 is not None else None),
                                 ((course.course_id, course.year, course.month,
                                   tuple((m.subject_ref, m.revision, m.student) for m in course.members))
                                  if course is not None else None))
        except (ValueError, TypeError, ValidationError):
            raise AuthenticationError() from None

    def verify_course_visitors(self, token: str):
        from pymentor_analytics.contracts.course_visitors import CourseVisitorGrant

        try:
            return CourseVisitorGrant.model_validate_json(
                self._verify(token, audience="analytics-course-visitors")
            )
        except ValidationError:
            raise AuthenticationError() from None

    def verify_course_study_time(self, token: str):
        from pymentor_analytics.contracts.course_visitors import CourseStudyTimeGrant

        try:
            return CourseStudyTimeGrant.model_validate_json(
                self._verify(token, audience="analytics-course-study-time")
            )
        except ValidationError:
            raise AuthenticationError() from None


class ProducerVerifier(_LocalVerifier):
    """Separate keyring, audience and scope; no control transport is exposed here."""
    def __init__(self, *, issuer: str, public_keys: Mapping[str, bytes], allowed_producers: set[str]):
        super().__init__(issuer=issuer, audience="analytics-control", public_keys=public_keys)
        try:
            if not allowed_producers or len(allowed_producers) > 100:
                raise ValueError("invalid producers")
            self._allowed_producers = frozenset(TypeAdapter(Identifier).validate_python(value, strict=True)
                                                for value in allowed_producers)
        except (TypeError, ValueError, ValidationError):
            raise ValueError("invalid verifier configuration") from None

    def verify(self, token: str) -> VerifiedProducer:
        try:
            claims = ProducerClaims.model_validate_json(self._verify(token))
            if claims.sub not in self._allowed_producers:
                raise ValueError("unknown producer")
            return VerifiedProducer(claims.sub, claims.scope, claims.exp)
        except (ValueError, TypeError, ValidationError):
            raise AuthenticationError() from None


def build_verifiers(*, grant_issuer: str, grant_keys: Mapping[str, bytes],
                    producer_issuer: str, producer_keys: Mapping[str, bytes],
                    allowed_producers: set[str]) -> tuple[GrantVerifier, ProducerVerifier]:
    """Require genuinely separate key material when enabling both trust domains."""
    grant = GrantVerifier(issuer=grant_issuer, public_keys=grant_keys)
    producer = ProducerVerifier(issuer=producer_issuer, public_keys=producer_keys, allowed_producers=allowed_producers)
    def raw_keys(verifier):
        return {key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                for key in verifier._keys.values()}
    if raw_keys(grant) & raw_keys(producer):
        raise ValueError("grant and producer keyrings must be separate")
    return grant, producer
