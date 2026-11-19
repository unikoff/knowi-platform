"""Cancelable, bounded HTTP exchange; never used in a Core business transaction."""
import asyncio
import httpx


async def request(method, url, *, headers, body, deadline_seconds, max_bytes, transport=None):
    if deadline_seconds <= 0 or max_bytes <= 0:
        return None
    try:
        async with asyncio.timeout(deadline_seconds):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(deadline_seconds), transport=transport,
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
