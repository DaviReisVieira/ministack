"""
Static File Server — serves the built React app from ministack/ui/dist/.
Handles MIME types and SPA fallback (serves index.html for unmatched routes).
"""

import os
from pathlib import Path

DIST_DIR = Path(__file__).parent / "dist"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
    ".txt": "text/plain; charset=utf-8",
}

UI_PREFIX = "/_ministack/ui"


async def serve(path: str, send):
    """Serve a static file or fall back to index.html for SPA routing."""
    # Strip the UI prefix to get the relative file path
    rel = path[len(UI_PREFIX):]
    if not rel or rel == "/":
        rel = "/index.html"

    # Prevent path traversal
    rel = rel.lstrip("/")
    if ".." in rel:
        await _send_error(send, 403, "Forbidden")
        return

    file_path = DIST_DIR / rel

    # If file exists, serve it directly
    if file_path.is_file():
        await _send_file(send, file_path)
        return

    # SPA fallback: if no extension, serve index.html (client-side routing)
    ext = os.path.splitext(rel)[1]
    if not ext:
        index = DIST_DIR / "index.html"
        if index.is_file():
            await _send_file(send, index)
            return

    # File not found
    await _send_error(send, 404, "Not Found")


async def _send_file(send, file_path: Path):
    """Read and send a file with correct MIME type."""
    ext = file_path.suffix.lower()
    content_type = MIME_TYPES.get(ext, "application/octet-stream")

    body = file_path.read_bytes()

    headers = [
        (b"content-type", content_type.encode("latin-1")),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-cache" if ext == ".html" else b"public, max-age=31536000, immutable"),
        (b"access-control-allow-origin", b"*"),
    ]

    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _send_error(send, status: int, message: str):
    """Send a simple error response."""
    body = message.encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})
