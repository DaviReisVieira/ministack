"""
UI Log Handler — captures log records for the dashboard log viewer.
Maintains a circular buffer and broadcasts to SSE subscribers.
"""

import asyncio
import logging
import time
from collections import deque

_MAX_BUFFER = int(__import__("os").environ.get("MINISTACK_UI_LOG_BUFFER", "500"))


class UILogHandler(logging.Handler):
    """Logging handler that buffers records and streams them to SSE clients."""

    def __init__(self, maxlen: int = _MAX_BUFFER):
        super().__init__()
        self.buffer: deque = deque(maxlen=maxlen)
        self.subscribers: list[asyncio.Queue] = []

    def emit(self, record: logging.LogRecord):
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }
        self.buffer.append(entry)
        for q in list(self.subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def get_recent(self, limit: int = 100) -> list[dict]:
        """Return recent log entries (most recent first)."""
        items = list(reversed(self.buffer))
        return items[:limit]

    async def subscribe(self):
        """Async generator that yields new log entries as they arrive."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.append(q)
        try:
            while True:
                entry = await q.get()
                yield entry
        finally:
            self.subscribers.remove(q)


# Module-level singleton — created once, attached to root logger in app.py
ui_log_handler = UILogHandler()
ui_log_handler.setFormatter(logging.Formatter("%(message)s"))
