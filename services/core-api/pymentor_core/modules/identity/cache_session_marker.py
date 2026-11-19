"""Non-authorizing cache identity for a cookie already authenticated by Core.

The key is random per app process. Restart or worker changes invalidate cache
identity rather than implying cookie or account equivalence. This marker is
not sufficient to protect a browser from concurrent cookie replacement.
"""
from hashlib import sha256
import hmac
import secrets


_DOMAIN = b'knowi:calendar:session-marker:v1\x00'


def new_marker_key():
    """Fail closed for caching if operating-system randomness is unavailable."""
    try:
        key = secrets.token_bytes(32)
    except Exception:
        return None
    return key if type(key) is bytes and len(key) == 32 else None


def marker_after_authentication(request):
    """Called only after successful cookie authentication and authorization."""
    key = getattr(request.app.state, 'calendar_session_marker_key', None)
    token = request.cookies.get('session')
    if type(key) is not bytes or len(key) != 32 or not isinstance(token, str):
        return None
    return hmac.new(key, _DOMAIN + token.encode('utf-8'), sha256).hexdigest()
