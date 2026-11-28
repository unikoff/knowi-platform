"""Isolate physical local TCP listeners while retaining the canonical signed HTTP endpoints."""
import asyncio

from httpcore._backends.anyio import AnyIOBackend

LOGICAL_PORTS = frozenset(range(18041, 18047))
ALLOWED_OFFSET = 1620


def install(offset: int) -> None:
    if offset == 0:
        return
    if offset != ALLOWED_OFFSET:
        raise ValueError("unexpected synthetic port offset")

    def physical(host, port):
        if host == "127.0.0.1" and port in LOGICAL_PORTS:
            return port + offset
        return port

    open_connection = asyncio.open_connection
    connect_tcp = AnyIOBackend.connect_tcp

    async def open_local(host=None, port=None, *args, **kwargs):
        return await open_connection(host, physical(host, port), *args, **kwargs)

    async def connect_local(self, host, port, *args, **kwargs):
        return await connect_tcp(self, host, physical(host, port), *args, **kwargs)

    asyncio.open_connection = open_local
    AnyIOBackend.connect_tcp = connect_local
