"""OpenDM local daemon — hardened HTTP + WebSocket API for browser integration."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
except ImportError:
    websockets = None

from opendm import __version__
from opendm.queue import QueueManager
from opendm.security import (
    load_or_create_config,
    origin_allowed,
    host_allowed,
    config_path,
)

CONFIG = load_or_create_config()
TOKEN = CONFIG["token"]
HTTP_PORT = int(CONFIG.get("http_port", 10010))
WS_PORT = int(CONFIG.get("ws_port", 10011))
ALLOWED_ORIGINS = list(CONFIG.get("allowed_origins", ["chrome-extension://*"]))
DEST_DIR = CONFIG.get("download_dir", os.path.join(os.path.expanduser("~"), "Downloads"))
EXT_WWW_DIR = os.path.join(os.path.dirname(config_path()), "www")  # crx + updates.xml

# -- hardening tunables (spec item 16) --------------------------------------
MAX_BODY_BYTES = 1024 * 1024          # 1 MiB cap on request bodies
SOCKET_TIMEOUT = 10                   # per-connection socket timeout, seconds
CONNECTIONS_MIN, CONNECTIONS_MAX = 1, 32  # valid range for 'connections'

QM = QueueManager(DEST_DIR)

EVENTS_LOG = os.path.join(os.path.dirname(config_path()), "events.log")


def log_event(msg: str):
    import datetime
    try:
        with open(EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


def pairing_active() -> bool:
    """True for 120s after an admin creates the 'pairing' flag file."""
    flag = os.path.join(os.path.dirname(config_path()), "pairing")
    if not os.path.exists(flag):
        return False
    age = time.time() - os.path.getmtime(flag)
    return age < 120


def _validate_connections(value):
    """Strictly validate the 'connections' JSON field.

    Returns (True, int) on success or (False, error_message). Rejects bools,
    non-ints and out-of-range values so a malformed request can never raise
    inside the handler.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False, "'connections' must be an integer"
    if not (CONNECTIONS_MIN <= value <= CONNECTIONS_MAX):
        return False, (f"'connections' out of range "
                       f"[{CONNECTIONS_MIN}-{CONNECTIONS_MAX}]")
    return True, value


class HardenedApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Per-connection socket timeout. StreamRequestHandler.setup() applies
    # self.timeout to the accepted socket, so an idle/slow client can never
    # stall the server (each connection runs on its own thread anyway).
    timeout = SOCKET_TIMEOUT

    # -- security filters -----------------------------------------------
    def _check_host(self) -> bool:
        h = self.headers.get("Host")
        if not host_allowed(h):
            self._send_error(400, "Invalid Host header (rebinding rejected)")
            return False
        return True

    def _check_auth(self) -> bool:
        tok = (self.headers.get("X-OpenDM-Token") or "").strip()
        if not tok or not hmac.compare_digest(tok, TOKEN):
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
        self.send_header("Connection", "close")
        self._cors_headers(self.headers.get("Origin"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_error(self, code: int, msg: str):
        self._send_json({"ok": False, "error": msg}, code=code)

    def _read_capped_body(self):
        """Read the request body, bounded by MAX_BODY_BYTES.

        Returns the body bytes (b"" when no body was declared), or None when
        an error response was already sent (400/413) or the connection died.
        Never reads more than MAX_BODY_BYTES + 1 bytes: a declared
        Content-Length above the cap, or a length-less (chunked) body that
        grows past the cap while reading, gets 413 and we stop reading.
        """
        length = self.headers.get("Content-Length")
        if length is not None:
            try:
                n = int(length.strip())
            except (ValueError, AttributeError):
                self._send_error(400, "Invalid Content-Length header")
                return None
            if n < 0:
                self._send_error(400, "Invalid Content-Length header")
                return None
            if n > MAX_BODY_BYTES:
                self._send_error(413, "Request body exceeds 1 MiB limit")
                return None
            if n == 0:
                return b""
            try:
                return self.rfile.read(n)
            except (socket.timeout, ConnectionError, OSError):
                self.close_connection = True
                return None
        # No Content-Length: only bound-read when the client claims a
        # streaming/chunked body; otherwise the message carries no body.
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" not in te:
            return b""
        try:
            data = self.rfile.read(MAX_BODY_BYTES + 1)
        except (socket.timeout, ConnectionError, OSError):
            self.close_connection = True
            return None
        if len(data) > MAX_BODY_BYTES:
            self._send_error(413, "Request body exceeds 1 MiB limit")
            return None
        return data

    # -- HTTP verbs -----------------------------------------------------
    def do_OPTIONS(self):
        if not self._check_host():
            return
        origin = self.headers.get("Origin")
        if not origin_allowed(origin, ALLOWED_ORIGINS):
            self._send_error(403, "Origin not allowed")
            return
        self.send_response(204)
        self.send_header("Connection", "close")
        self._cors_headers(origin)
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        if not self._check_host():
            return
        origin = self.headers.get("Origin")
        if origin and not origin_allowed(origin, ALLOWED_ORIGINS):
            self._send_error(403, "Origin not allowed")
            return

        path = urllib.parse.urlparse(self.path).path

        # -- public (no token): extension serving + pairing (time-boxed) ----
        if path in ("/ext/manifest.crx", "/ext/updates.xml"):
            return self._serve_extension_file(path)
        if path == "/api/v1/pair":
            return self._handle_pair()

        if not self._check_auth():
            return
        if path == "/api/v1/status":
            self._send_json({"ok": True, "service": "opendm",
                             "version": __version__})
        elif path == "/api/v1/jobs":
            self._send_json({"ok": True, "jobs": QM.snapshot()})
        else:
            self._send_error(404, "Unknown endpoint")

    # -- extension install/serve (self-hosted CRX update flow) --------------
    def _serve_extension_file(self, path):
        name = os.path.basename(path)
        fp = os.path.join(EXT_WWW_DIR, name)
        if not os.path.isfile(fp):
            self._send_error(404, f"{name} not found (run opendm.packext first)")
            return
        ctype = "application/x-chrome-extension" if name.endswith(".crx") else "text/xml"
        size = os.path.getsize(fp)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with open(fp, "rb") as f:
                while True:
                    b = f.read(65536)
                    if not b:
                        break
                    self.wfile.write(b)
        except (ConnectionError, OSError):
            pass
        self.close_connection = True

    def _handle_pair(self):
        if not pairing_active():
            self._send_error(403, "Pairing window closed")
            return
        # Reject non-extension origin
        origin = self.headers.get("Origin") or ""
        if not origin.startswith(("chrome-extension://", "moz-extension://")):
            self._send_error(403, "Pairing allowed only from browser extension origin")
            return
        log_event(f"PAIR granted from Origin={origin}")
        self._send_json({"ok": True, "token": TOKEN})

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

        body = self._read_capped_body()
        if body is None:
            return  # 400/413 already sent, or the connection died
        try:
            data = json.loads(body or b"{}")
        except Exception:
            self._send_error(400, "Invalid JSON body")
            return
        if not isinstance(data, dict):
            self._send_error(400, "JSON body must be an object")
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
        # 'suggested_filename' is accepted but sanitized to a bare filename.
        ok, conns = _validate_connections(data.get("connections", 4))
        if not ok:
            self._send_error(400, conns)
            return
        extra_headers = data.get("headers") or {}
        if not isinstance(extra_headers, dict):
            self._send_error(400, "'headers' must be an object")
            return
        safe_headers = {k: v for k, v in extra_headers.items()
                        if isinstance(k, str)
                        and k.lower() in ("cookie", "referer", "user-agent")}

        suggested = data.get("suggested_filename")
        show_prompt = bool(data.get("show_dialog", True))

        # Check if GUI registered an intercept prompt hook
        on_prompt = getattr(QM, "on_new_download_request", None)
        if on_prompt and show_prompt:
            # Let GUI present the prompt to the user
            threading.Thread(target=on_prompt, args=(url, conns, safe_headers, suggested), daemon=True).start()
            self._send_json({"ok": True, "status": "prompted", "message": "Download confirmation shown in GUI"})
            return

        job = QM.add(url, conns=conns, headers=safe_headers,
                     suggested_filename=suggested if isinstance(suggested, str) else None)
        job.start()
        self._send_json({"ok": True, "job_id": job.id, "status": job.state,
                         "saved_to_dir": DEST_DIR, "filename": job.filename})

    def log_message(self, *a):
        pass


class ThreadedHTTPServer(ThreadingHTTPServer):
    """Thread-per-connection HTTP server (daemon threads).

    Replaces the old single-threaded HTTPServer so one slow/idle client can
    never stall the daemon. Bind failures raise a clear RuntimeError naming
    the address instead of a bare OSError.
    """
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_class=HardenedApiHandler):
        try:
            super().__init__(server_address, handler_class)
        except OSError as ex:
            raise RuntimeError(
                "OpenDM daemon: cannot bind "
                f"{server_address[0]}:{server_address[1]} "
                f"(port already in use?): {ex}"
            ) from ex


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
        token = first.get("token")
        if (first.get("cmd") == "auth" and isinstance(token, str)
                and hmac.compare_digest(token, TOKEN)):
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
                ok, conns = _validate_connections(data.get("connections", 4))
                if not ok:
                    await ws.send(json.dumps({"error": conns}))
                    continue
                extra_headers = data.get("headers") or {}
                if not isinstance(extra_headers, dict):
                    await ws.send(json.dumps({"error": "'headers' must be an object"}))
                    continue
                safe = {k: v for k, v in extra_headers.items()
                        if isinstance(k, str)
                        and k.lower() in ("cookie", "referer", "user-agent")}
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
    try:
        server = await websockets.serve(ws_handler, "127.0.0.1", WS_PORT)
    except OSError as ex:
        print(f"ERROR: OpenDM daemon: cannot bind WebSocket 127.0.0.1:{WS_PORT} "
              f"(port already in use?): {ex}", file=sys.stderr)
        raise SystemExit(1)
    async with server:
        print(f"OpenDM WebSocket on 127.0.0.1:{WS_PORT}")
        while True:
            await asyncio.sleep(3600)


def main():
    print(f"OpenDM daemon starting (HTTP:{HTTP_PORT}, WS:{WS_PORT})")
    try:
        httpd = ThreadedHTTPServer(("127.0.0.1", HTTP_PORT), HardenedApiHandler)
    except RuntimeError as ex:
        print(f"ERROR: {ex}", file=sys.stderr)
        raise SystemExit(1)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"OpenDM HTTP API on 127.0.0.1:{HTTP_PORT}")
    if websockets:
        asyncio.run(ws_loop())
    else:
        while True:
            threading.Event().wait(3600)


if __name__ == "__main__":
    main()
