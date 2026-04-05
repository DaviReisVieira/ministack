"""
Request Interceptor — captures every AWS API request for the UI dashboard.
Stores entries in a circular buffer and broadcasts to SSE subscribers.
"""

import asyncio
import time
import uuid
from collections import deque

_MAX_BUFFER = int(__import__("os").environ.get("MINISTACK_UI_REQUEST_BUFFER", "1000"))
_buffer: deque = deque(maxlen=_MAX_BUFFER)
_subscribers: list[asyncio.Queue] = []
_start_time: float = time.monotonic()


def record_request(*, method: str, path: str, service: str, action: str,
                   status: int, duration_ms: float, request_size: int,
                   response_size: int):
    """Record a completed request into the buffer and notify SSE subscribers."""
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "method": method,
        "path": path,
        "service": service,
        "action": action,
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "request_size": request_size,
        "response_size": response_size,
    }
    _buffer.append(entry)
    for q in list(_subscribers):
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass


def get_requests(limit: int = 50, offset: int = 0) -> dict:
    """Return recent requests from the buffer (most recent first)."""
    items = list(reversed(_buffer))
    total = len(items)
    page = items[offset:offset + limit]
    return {"requests": page, "total": total}


def get_uptime() -> float:
    """Return seconds since MiniStack started."""
    return round(time.monotonic() - _start_time, 1)


async def subscribe():
    """Async generator that yields new request entries as they arrive."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(q)
    try:
        while True:
            entry = await q.get()
            yield entry
    finally:
        _subscribers.remove(q)


def extract_action(headers: dict, query_params: dict, path: str) -> str:
    """Extract the AWS action name from request metadata."""
    # JSON services: X-Amz-Target header (e.g., DynamoDB_20120810.PutItem)
    target = headers.get("x-amz-target", "")
    if target and "." in target:
        return target.split(".")[-1]

    # Query/XML services: Action query param
    action = query_params.get("Action", [""])[0] if isinstance(
        query_params.get("Action"), list) else query_params.get("Action", "")
    if action:
        return action

    # REST services: extract from URL path
    parts = [p for p in path.strip("/").split("/") if p]
    if parts:
        return parts[-1]

    return ""
