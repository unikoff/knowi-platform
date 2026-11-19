"""Reusable ASGI bearer extraction; no product routes are installed here."""
from collections.abc import Iterable

from pymentor_analytics.contracts.auth_v1 import AuthenticationError, MAX_TOKEN_BYTES


def extract_bearer(headers: Iterable[tuple[bytes, bytes]]) -> str:
    values = [value for name, value in headers if name.lower() == b"authorization"]
    if len(values) != 1:
        raise AuthenticationError()
    value = values[0]
    if not isinstance(value, bytes) or len(value) > MAX_TOKEN_BYTES + 7:
        raise AuthenticationError()
    try:
        scheme, token = value.split(b" ", 1)
        if scheme.lower() != b"bearer" or not token or any(char in token for char in (b" ", b"\t", b"\r", b"\n")):
            raise ValueError
        return token.decode("ascii")
    except (ValueError, UnicodeError):
        raise AuthenticationError() from None
