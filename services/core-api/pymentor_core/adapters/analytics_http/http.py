"""Cancelable, bounded HTTP exchange; never used in a Core business transaction."""
import asyncio
import httpx


def client_tls(env, prefix, endpoint):
    """Build a verified mTLS context for a remote service, never a fallback."""
    import ssl
    from urllib.parse import urlsplit

    url = urlsplit(endpoint)
    values = tuple(env.get(prefix + suffix) for suffix in ("_CA_FILE", "_CLIENT_CERT_FILE", "_CLIENT_KEY_FILE"))
    if not any(values) and url.hostname in {"127.0.0.1", "localhost", "::1"}:
        return True
    if not all(values) or url.scheme != "https":
        raise ValueError("Analytics mutual TLS configuration required")
    context = ssl.create_default_context(cafile=values[0])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=values[1], keyfile=values[2])
    return context


async def request(method, url, *, headers, body, deadline_seconds, max_bytes, transport=None, verify=True):
    if deadline_seconds <= 0 or max_bytes <= 0:
        return None
    try:
        async with asyncio.timeout(deadline_seconds):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(deadline_seconds), transport=transport, verify=verify,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                async with client.stream(method, url, headers={**headers, "Accept-Encoding": "identity"}, content=body) as response:
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        return None
                    chunks = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(chunks) + len(chunk) > max_bytes:
                            return None
                        chunks.extend(chunk)
                    return response.status_code, bytes(chunks)
    except (TimeoutError, httpx.HTTPError, ValueError):
        return None
