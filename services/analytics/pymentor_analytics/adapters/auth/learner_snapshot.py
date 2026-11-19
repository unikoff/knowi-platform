"""Pinned local Core signing key and strict snapshot grant verification."""
import hashlib
import hmac
import re
from uuid import UUID

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from pymentor_analytics.adapters.auth.verifier import _parts


class InvalidGrant(Exception):
    pass


class ForbiddenGrant(Exception):
    pass


def load_key(path: str | None):
    if not path:
        return None
    try:
        with open(path, "rb") as source:
            pem = source.read(4097)
        if not 1 <= len(pem) <= 4096:
            return None
        key = serialization.load_pem_public_key(pem)
        return key if isinstance(key, Ed25519PublicKey) else None
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
        return None


def verify_grant(token: str, key: Ed25519PublicKey, body: bytes) -> str:
    try:
        header, _ = _parts(token)
        if header.kid != "learner-preview-1":
            raise ValueError("invalid kid")
        claims = jwt.decode(token, key=key, algorithms=["EdDSA"], issuer="pymentor-core",
                            audience="analytics-learner-report", leeway=0,
                            options={"require": ["iss", "aud", "sub", "iat", "exp", "scope", "snapshot_sha256"], "strict_aud": True})
        if type(claims["iat"]) is not int or type(claims["exp"]) is not int or not 0 < claims["exp"] - claims["iat"] <= 60:
            raise ValueError("invalid lifetime")
        sub = claims["sub"]
        if not isinstance(sub, str) or str(UUID(sub)) != sub:
            raise ValueError("invalid subject")
        digest = claims["snapshot_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not hmac.compare_digest(digest, hashlib.sha256(body).hexdigest()):
            raise ValueError("invalid digest")
        if not isinstance(claims["scope"], str):
            raise ValueError("invalid scope")
        if claims["scope"] != "learner:report":
            raise ForbiddenGrant()
        return sub
    except ForbiddenGrant:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, jwt.PyJWTError, RecursionError):
        raise InvalidGrant() from None
