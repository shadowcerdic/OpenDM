"""Shared fault-injection HTTP fixture for OpenDM tests.

Provides a local ThreadingHTTPServer serving a deterministic binary payload with
switchable misbehaviors:

  cfg["truncate_after"]   int | None   - send only the first N bytes of the
                                         (range-selected) body, close-delimited
                                         (no Content-Length), then close the
                                         connection abruptly.
  cfg["truncate_cl"]      int | None   - same but WITH Content-Length set to the
                                         full length (client sees IncompleteRead).
  cfg["weak_etag"]        bool         - serve ETag: W/"..." instead of strong.
  cfg["ignore_range"]     bool         - ignore Range, answer 200 + full body.
  cfg["redirect_to"]      str | None   - answer 302 with Location=<value>.
  cfg["slow"]             float        - seconds of delay between 64 KiB chunks.
  cfg["paths"]            dict         - per-path overrides:
                                         {"/p": {"status": 503, "retry_after": 1}}
  cfg["no_ranges"]        bool         - omit Accept-Ranges (single-stream).

The server records every request it sees in server.requests as
{"method", "path", "headers"} for header-leak assertions.
"""

import hashlib
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PAYLOAD_SIZE = 8 * 1024 * 1024
_rng = random.Random(0x0D4D)
PAYLOAD = _rng.randbytes(PAYLOAD_SIZE)
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
STRONG_ETAG = '"0d4d-etag-1"'
WEAK_ETAG = 'W/"0d4d-etag-1"'
CHUNK = 64 * 1024


class FaultHandler(BaseHTTPRequestHandler):
    server_version = "FaultSrv/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _record(self):
        self.server.requests.append({
            "method": self.command,
            "path": self.path.split("?")[0],
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })

    def _etag(self):
        return WEAK_ETAG if self.server.cfg.get("weak_etag") else STRONG_ETAG

    def _send_head(self, path, body_len, status=206, content_range=None,
                   close_delimited=False):
        self.send_response(status)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", self._etag())
        self.send_header("Last-Modified", "Wed, 01 Jan 2025 00:00:00 GMT")
        self.send_header("Content-Type", "application/octet-stream")
        if content_range:
            self.send_header("Content-Range", content_range)
        if not close_delimited:
            self.send_header("Content-Length", str(body_len))
        self.send_header("Connection", "close")
        self.end_headers()

    def _select(self, path):
        """Return (status, start, end, headers_extra) for a GET."""
        cfg = self.server.cfg
        if cfg.get("redirect_to") and path != "/health":
            return ("redirect", 0, 0, {})
        total = PAYLOAD_SIZE
        rng = self.headers.get("Range")
        if cfg.get("ignore_range") or not rng:
            return (200, 0, total - 1, {})
        # parse "bytes=a-b"
        try:
            unit, spec = rng.split("=", 1)
            a, b = spec.split("-", 1)
            start = int(a) if a else 0
            end = int(b) if b else total - 1
        except Exception:
            return (416, 0, 0, {})
        start = max(0, min(start, total - 1))
        end = max(start, min(end, total - 1))
        return (206, start, end, {})

    def _serve_body(self, start, end, close_delimited=False):
        cfg = self.server.cfg
        data = PAYLOAD[start:end + 1]
        trunc = cfg.get("truncate_after")
        if trunc is not None:
            data = data[:trunc]
            close_delimited = True
        trunc_cl = cfg.get("truncate_cl")
        if trunc_cl is not None:
            data = data[:trunc_cl]  # Content-Length stays full -> IncompleteRead
        slow = cfg.get("slow", 0)
        try:
            for i in range(0, len(data), CHUNK):
                self.wfile.write(data[i:i + CHUNK])
                self.wfile.flush()
                if slow:
                    time.sleep(slow)
        except (BrokenPipeError, ConnectionResetError):
            pass
        # Abrupt close: do not send anything more; handler returns and the
        # server closes the socket (Connection: close was sent).
        self.close_connection = True

    def _handle(self, head_only):
        self._record()
        cfg = self.server.cfg
        path = self.path.split("?")[0]
        if path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            self.close_connection = True
            return
        pov = cfg.get("paths", {}).get(path)
        if pov:
            status = pov.get("status", 200)
            body = b"err"
            self.send_response(status)
            if "retry_after" in pov:
                self.send_header("Retry-After", str(pov["retry_after"]))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            self.close_connection = True
            return
        if cfg.get("redirect_to"):
            self.send_response(302)
            self.send_header("Location", cfg["redirect_to"])
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return
        total = PAYLOAD_SIZE
        sel = self._select(path)
        if sel[0] == 416:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return
        status, start, end = sel[0], sel[1], sel[2]
        if head_only:
            self._send_head(path, total if status == 200 else end - start + 1,
                            status=status,
                            content_range=(f"bytes {start}-{end}/{total}"
                                           if status == 206 else None))
            self.close_connection = True
            return
        if status == 200:
            trunc = cfg.get("truncate_after")
            close_delim = trunc is not None
            body_len = total if not close_delim else min(trunc, total)
            # for truncate_cl the declared length stays full
            declared = total
            self._send_head(path, declared, status=200,
                            close_delimited=close_delim)
            self._serve_body(start, end, close_delimited=close_delim)
        else:
            trunc = cfg.get("truncate_after")
            close_delim = trunc is not None
            seg_len = end - start + 1
            declared = seg_len
            self._send_head(path, declared, status=206,
                            content_range=f"bytes {start}-{end}/{total}",
                            close_delimited=close_delim)
            self._serve_body(start, end, close_delimited=close_delim)

    def do_GET(self):
        self._handle(head_only=False)

    def do_HEAD(self):
        self._handle(head_only=True)


class FaultServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})
        self.requests = []
        super().__init__(("127.0.0.1", 0), FaultHandler)
        self._thread = threading.Thread(target=self.serve_forever,
                                        kwargs={"poll_interval": 0.05},
                                        daemon=True)

    def url(self, path="/file.bin"):
        host, port = self.server_address
        return f"http://{host}:{port}{path}"

    def __enter__(self):
        self._thread.start()
        # wait until serving
        for _ in range(100):
            try:
                import urllib.request
                urllib.request.urlopen(self.url("/health"), timeout=1).read()
                break
            except Exception:
                time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


def make_server(cfg=None):
    return FaultServer(cfg)
