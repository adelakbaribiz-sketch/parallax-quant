"""Local research console: a stdlib HTTP server that serves the static
front-end in ./static and one JSON endpoint, /api/snapshot, computed by
snapshot.build_snapshot() from the engine's real strategy and risk code.

Binds to 127.0.0.1 by default. It has no write endpoints and never touches
an execution adapter, so it is safe to run next to a paper-trading
orchestrator.

    python -m parallax_quant.dashboard --port 8050
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import threading
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog

from parallax_quant.dashboard.snapshot import build_snapshot

STATIC_DIR = Path(__file__).parent / "static"
_MAX_SEED = 2**32 - 1

logger = structlog.get_logger(__name__)


class SnapshotCache:
    """Snapshots are deterministic per (config, seed), so each seed is
    computed once per process."""

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self._lock = threading.Lock()
        self._by_seed: dict[int, bytes] = {}

    def get(self, seed: int) -> bytes:
        with self._lock:
            if seed not in self._by_seed:
                snapshot = asyncio.run(build_snapshot(self.config_path, seed))
                self._by_seed[seed] = json.dumps(snapshot, allow_nan=False).encode("utf-8")
            return self._by_seed[seed]


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "ParallaxConsole/0.1"

    def __init__(self, *args: Any, cache: SnapshotCache, default_seed: int, **kwargs: Any) -> None:
        self.cache = cache
        self.default_seed = default_seed
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/api/snapshot":
            self._snapshot(parse_qs(url.query))
            return
        self._static(url.path)

    def _snapshot(self, query: dict[str, list[str]]) -> None:
        raw = query.get("seed", [str(self.default_seed)])[0]
        try:
            seed = int(raw)
            if not 0 <= seed <= _MAX_SEED:
                raise ValueError
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"seed must be an integer in [0, 2^32)"}', "application/json")
            return
        self._send(HTTPStatus.OK, self.cache.get(seed), "application/json")

    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/j")) else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("http_request", line=format % args)


def serve(host: str, port: int, config_path: str, seed: int) -> None:
    cache = SnapshotCache(config_path)
    handler = partial(ConsoleHandler, cache=cache, default_seed=seed)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        logger.info("research_console_started", url=f"http://{host}:{port}", config=config_path, seed=seed)
        httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallax Quant research console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--seed", type=int, default=8)
    args = parser.parse_args()
    serve(args.host, args.port, args.config, args.seed)


if __name__ == "__main__":
    main()
