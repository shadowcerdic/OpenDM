"""OpenDM local daemon — hardened HTTP + WebSocket API for browser integration."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
except ImportError:
    websockets = None

from opendm.queue import QueueManager
from opendm.security import (
    load_or_create_config,
    origin_allowed,
    host_allowed,
)

CONFIG = load_or_create_config()
TOKEN = CONFIG["token"]
HTTP_PORT = int(CONFIG.get("http_port", 10010))
WS_PORT = int(CONFIG.get("ws_port", 10011))
ALLOWED_ORIGINS = list(CONFIG.get("allowed_origins", ["chrome-extension://*"]))
DEST_DIR = CONFIG.get("download_dir", os.path.join(os.path.expanduser("~"), "Downloads"))

QM = QueueManager(DEST_DIR)


class HardenedApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- security filters -----------------------------------------------
    def _check_host(self) -> bool:
        h = self.headers.get("Host")
        if not host_allowed(h):
            self._send_error(400, "Invalid Host header (rebinding rejected)")
            return False
        return True

    def _check_auth(self) -> bool:
        tok = self.headers.get("X-OpenDM-Token") or ""
        if not tok or tok.strip() != TOKEN:
            self._send_error(401, "Missing or invalid X-OpenDM-Token header")
            return False
        return True

    def _cors_headers(self, origin: str | None):
        if origin and origin_allowed(origin, ALLOWED_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-OpenDM-Token")
            self.send_header("Vary", "Origin")

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers(self.headers.get("Origin"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, msg: str):
        self._send_json({"ok": False, "error": msg}, code=code)

    # -- HTTP verbs -----------------------------------------------------
    def do_OPTIONS(self):
        if not self._check_host():
            return
        origin = self.headers.get("Origin")
        if not origin_allowed(origin, ALLOWED_ORIGINS):
            self._send_error(403, "Origin not allowed")
            return
        self.send_response(204)
        self._cors_headers(origin)
        self.end_headers()

    def do_GET(self):
        if not self._check_host():
            return
        # Origin must either be absent (direct tool/curl) or allowed
        origin = self.headers.get("Origin")
        if origin and not origin_allowed(origin, ALLOWED_ORIGINS):
            self._send_error(403, "Origin not allowed")
            return
        if not self._check_auth():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/v1/status":
            self._send_json({"ok": True, "service": "opendm", "version": "1.0.0"})
        elif path == "/api/v1/jobs":
            self._send_json({"ok": True, "jobs": QM.snapshot()})
        else:
            self._send_error(404, "Unknown endpoint")

    def do_POST(self):
        if not self._check_host():
            return
        origin = self.headers.get("Origin")
        if origin and not origin_allowed(origin, ALLOWED_ORIGINS):
            self._send_error(403, "Origin not allowed")
            return
        if not self._check_auth():
            return
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/v1/download":
            self._send_error(404, "Unknown endpoint")
            return

        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send_error(400, "Invalid JSON body")
            return

        url = data.get("url")
        if not url or not isinstance(url, str):
            self._send_error(400, "Missing 'url' field")
            return
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            self._send_error(400, f"Unsupported scheme: {parsed.scheme!r} (http/https only)")
            return

        # Security: 'dest', 'dest_dir', 'path' fields are deliberately ignored;
        # destination ALWAYS comes from server config, never from request payload.
        conns = int(data.get("connections", 4))
        extra_headers = data.get("headers") or {}
        safe_headers = {k: v for k, v in extra_headers.items()
                        if k.lower() in ("cookie", "referer", "user-agent")}

        job = QM.add(url, conns=conns, headers=safe_headers)
        job.start()
        self._send_json({"ok": True, "job_id": job.id, "status": job.state,
                         "saved_to_dir": DEST_DIR})

    def log_message(self, *a):
        pass


# -- WebSocket server with explicit Origin and Token handshake ---------
async def ws_handler(ws):
    headers = getattr(ws, "request", None)
    origin = None
    if headers and hasattr(headers, "headers"):
        origin = headers.headers.get("origin") or headers.headers.get("Origin")
    if origin and not origin_allowed(origin, ALLOWED_ORIGINS):
        await ws.close(code=4403, reason="Origin not allowed")
        return

    authenticated = False
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        first = json.loads(raw)
        if first.get("cmd") == "auth" and first.get("token") == TOKEN:
            authenticated = True
            await ws.send(json.dumps({"type": "auth_ok"}))
        else:
            await ws.send(json.dumps({"type": "auth_failed"}))
            await ws.close(code=4401, reason="Authentication failed")
            return
    except Exception:
        await ws.close(code=4401, reason="Authentication timeout or malformed")
        return

    async for message in ws:
        if not authenticated:
            return
        try:
            data = json.loads(message)
            cmd = data.get("cmd")
            if cmd == "download":
                url = data.get("url", "")
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme not in ("http", "https"):
                    await ws.send(json.dumps({"error": "invalid scheme"}))
                    continue
                conns = int(data.get("connections", 4))
                extra_headers = data.get("headers") or {}
                safe = {k: v for k, v in extra_headers.items()
                        if k.lower() in ("cookie", "referer", "user-agent")}
                job = QM.add(url, conns=conns, headers=safe)
                job.start()
                await ws.send(json.dumps({"type": "job_created", "job_id": job.id}))
            elif cmd == "query":
                await ws.send(json.dumps({"type": "jobs_status", "jobs": QM.snapshot()}))
            else:
                await ws.send(json.dumps({"type": "pong"}))
        except Exception as ex:
            await ws.send(json.dumps({"error": str(ex)}))


async def ws_loop():
    async with websockets.serve(ws_handler, "127.0.0.1", WS_PORT):
        print(f"OpenDM WebSocket on 127.0.0.1:{WS_PORT}")
        while True:
            await asyncio.sleep(3600)


def main():
    print(f"OpenDM daemon starting (HTTP:{HTTP_PORT}, WS:{WS_PORT})")
    httpd = HTTPServer(("127.0.0.1", HTTP_PORT), HardenedApiHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"OpenDM HTTP API on 127.0.0.1:{HTTP_PORT}")
    if websockets:
        asyncio.run(ws_loop())
    else:
        while True:
            threading.Event().wait(3600)


if __name__ == "__main__":
    main()
