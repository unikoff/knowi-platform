"""Opt-in, bounded same-origin transport to the Analytics Collector."""
import asyncio
import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from pymentor_core.adapters.analytics_http.http import client_tls, request as http_request

PATH = "/api/analytics/events/batch"
MAX_BODY = 256 * 1024
MAX_REPLY = 256 * 1024
DEADLINE = 10.0
MAX_IN_FLIGHT = 16
_BEARER = re.compile(r"Bearer [A-Za-z0-9._~-]{1,8192}\Z", re.ASCII)
_STATUSES = {200, 400, 401, 403, 413, 429, 503}
router = APIRouter()


def configure(env):
    origin = env.get("PYMENTOR_ANALYTICS_BROWSER_COLLECTOR_ORIGIN")
    if origin is None or origin == "":
        return None
    try:
        url = urlsplit(origin)
        host = url.hostname
        if (url.scheme != "https" or not host or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment or "?" in origin or "#" in origin
                or any(c.isspace() or ord(c) < 32 for c in origin)):
            raise ValueError
        port = url.port
        if port == 0 or origin.endswith(":") or "\\" in origin or "%" in host:
            raise ValueError
        normalized_host = host.lower().rstrip(".")
        if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if (not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or re.fullmatch(r"[0-9.]+", host)
                    or re.fullmatch(r"0[xX][0-9a-fA-F]+", host)):
                raise ValueError
        if address is not None and (address.is_loopback or
                (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
                 and address.ipv4_mapped.is_loopback)):
            raise ValueError
        if not all(env.get("PYMENTOR_ANALYTICS_BROWSER_COLLECTOR" + suffix)
                   for suffix in ("_CA_FILE", "_CLIENT_CERT_FILE", "_CLIENT_KEY_FILE")):
            raise ValueError
        verify = client_tls(env, "PYMENTOR_ANALYTICS_BROWSER_COLLECTOR", origin)
    except (ValueError, OSError):
        raise ValueError("Invalid Analytics browser Collector configuration") from None
    return {"endpoint": origin + PATH, "verify": verify, "slots": asyncio.Semaphore(MAX_IN_FLIGHT)}


def _error(status, detail):
    return JSONResponse(status_code=status, content={"detail": detail}, headers={"Cache-Control": "no-store"})


@router.post(PATH)
async def browser_batch(request: Request):
    config = request.app.state.analytics_browser_gateway
    authorization = [value for name, value in request.scope["headers"] if name.lower() == b"authorization"]
    if len(authorization) != 1:
        return _error(401, "Invalid credential")
    try:
        bearer = authorization[0].decode("ascii")
    except UnicodeDecodeError:
        return _error(401, "Invalid credential")
    if not _BEARER.fullmatch(bearer):
        return _error(401, "Invalid credential")
    if request.scope.get("query_string") or request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return _error(400, "Invalid request")
    if config["slots"].locked():
        return _error(503, "Collector unavailable")
    try:
        async with asyncio.timeout(DEADLINE):
            async with config["slots"]:
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        return _error(413, "Batch too large")
                    body.extend(chunk)
                result = await http_request("POST", config["endpoint"], headers={"Authorization": bearer,
                    "Content-Type": "application/json"}, body=bytes(body), deadline_seconds=DEADLINE,
                    max_bytes=MAX_REPLY, verify=config["verify"], include_safe_headers=True)
    except TimeoutError:
        return _error(503, "Collector unavailable")
    if result is None or result[0] not in _STATUSES:
        return _error(503, "Collector unavailable")
    return Response(result[1], status_code=result[0], media_type="application/json",
                    headers={"Cache-Control": "no-store", **result[2]})
