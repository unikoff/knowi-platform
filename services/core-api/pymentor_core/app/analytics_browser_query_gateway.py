"""Restricted browser report routes to the configured private Analytics Query."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import Response

from pymentor_core.app import analytics_browser_gateway as collector
from pymentor_core.adapters.analytics_http.http import request as http_request

PREFIX = "/api/analytics/reports/activity"
ROUTES = {
    PREFIX: "GET",
    PREFIX + "/details": "GET",
    PREFIX + "/calendar": "POST",
    PREFIX + "/chapters": "POST",
    PREFIX + "/outline": "POST",
    PREFIX + "/outline/lifetime": "POST",
    PREFIX + "/outline/lifetime/student": "POST",
}
MAX_BODY = 256 * 1024
MAX_QUERY = 8192
MAX_REPLY = 2 * 1024 * 1024
DEADLINE = 10.0
MAX_IN_FLIGHT = 16
STATUSES = {200, 400, 401, 403, 404, 405, 413, 422, 429, 503}
router = APIRouter()


def configure(env):
    enabled = env.get("PYMENTOR_ANALYTICS_BROWSER_QUERY_ENABLED", "0")
    if enabled == "0":
        return None
    if enabled != "1":
        raise ValueError("Invalid Analytics browser Query configuration")
    origin = env.get("PYMENTOR_ANALYTICS_QUERY_URL", "")
    # Reuse the Collector's strict remote-origin and mTLS validation, while
    # retaining the existing private Query endpoint and its credentials.
    mapped = {"PYMENTOR_ANALYTICS_BROWSER_COLLECTOR_ORIGIN": origin.rstrip("/")}
    for suffix in ("_CA_FILE", "_CLIENT_CERT_FILE", "_CLIENT_KEY_FILE"):
        mapped["PYMENTOR_ANALYTICS_BROWSER_COLLECTOR" + suffix] = env.get("PYMENTOR_ANALYTICS_QUERY" + suffix)
    try:
        if not origin or origin.endswith("//"):
            raise ValueError
        config = collector.configure(mapped)
        if config is None:
            raise ValueError
    except (ValueError, OSError):
        raise ValueError("Invalid Analytics browser Query configuration") from None
    return {"origin": origin.rstrip("/"), "verify": config["verify"], "slots": asyncio.Semaphore(MAX_IN_FLIGHT)}


async def browser_query(request: Request):
    config = request.app.state.analytics_browser_query_gateway
    auth = [value for name, value in request.scope["headers"] if name.lower() == b"authorization"]
    try:
        bearer = auth[0].decode("ascii") if len(auth) == 1 else ""
    except UnicodeDecodeError:
        bearer = ""
    if not collector._BEARER.fullmatch(bearer):
        return collector._error(401, "Invalid credential")
    query = request.scope.get("query_string", b"")
    if (len(query) > MAX_QUERY or any(c < 33 or c > 126 or c == 35 for c in query)
            or (request.method == "POST" and query)):
        return collector._error(400, "Invalid request")
    if request.method == "POST" and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return collector._error(400, "Invalid request")
    if config["slots"].locked():
        return collector._error(503, "Query unavailable")
    try:
        async with asyncio.timeout(DEADLINE):
            async with config["slots"]:
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        return collector._error(413, "Request too large")
                    body.extend(chunk)
                if request.method == "GET" and body:
                    return collector._error(400, "Invalid request")
                headers = {"Authorization": bearer, "Accept": "application/json"}
                if request.method == "POST":
                    headers["Content-Type"] = "application/json"
                url = config["origin"] + request.url.path + (("?" + query.decode("ascii")) if query else "")
                result = await http_request(request.method, url, headers=headers,
                    body=bytes(body) if request.method == "POST" else None, deadline_seconds=DEADLINE,
                    max_bytes=MAX_REPLY, verify=config["verify"], include_safe_headers=True)
    except TimeoutError:
        return collector._error(503, "Query unavailable")
    if result is None or result[0] not in STATUSES:
        return collector._error(503, "Query unavailable")
    return Response(result[1], status_code=result[0], media_type="application/json",
                    headers={"Cache-Control": "no-store", **result[2]})


for path, method in ROUTES.items():
    router.add_api_route(path, browser_query, methods=[method], include_in_schema=False)
