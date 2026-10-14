"""Fault-injection tests for the engine fix items 1-10.

Uses the shared fixture from tests/conftest.py (local ThreadingHTTPServer with
switchable misbehaviors) -- no live internet.
"""
from __future__ import annotations

import hashlib
import http.server
import threading
import time

import pytest

from conftest import make_server, PAYLOAD, PAYLOAD_SHA256
from opendm import engine


# ---------------------------------------------------------------------------
# Item 1: short reads are retryable errors; 206 Content-Range start verified
# ---------------------------------------------------------------------------

def test_truncated_body_is_retryable_and_surfaces_error(tmp_path):
    """A close-delimited truncated body must not silently succeed.

    Review repro: truncation. Server sends only 128 bytes then closes abruptly;
    the segment must exhaust retries and surface an error (not return success
    with a 128-byte cursor on an 8 MiB segment).
    """
    with make_server({"truncate_after": 128}) as server:
        dest = str(tmp_path / "trunc.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, len(PAYLOAD) - 1)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            with pytest.raises(RuntimeError, match=r"(?i)(short.?read|truncat|network error)"):
                engine._download_single_segment(
                    url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                    stop_event=None, on_bytes=None, extra_headers=None,
                    etag=None, meta_saver=None, max_retries=2,
                )
        # Must have actually retried (initial attempt + retries), not given up at once.
        gets = [r for r in server.requests
                if r["method"] == "GET" and r["path"] == "/file.bin"]
        assert len(gets) == 3, f"expected 1 initial + 2 retries, saw {len(gets)} GETs"


def test_short_read_retries_and_recovers(tmp_path):
    """A transient truncation must be recovered by retry: final sha matches."""
    with make_server({"truncate_after": 64 * 1024}) as server:
        dest = str(tmp_path / "rec.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, len(PAYLOAD) - 1)
        lock = threading.Lock()

        def heal_after_second_get():
            # Wait until the 2nd GET has been recorded (1st + 2nd both truncated
            # at serve time), then heal so the next retry gets the full body.
            def file_gets():
                return sum(1 for r in server.requests
                           if r["method"] == "GET" and r["path"] == "/file.bin")
            for _ in range(400):
                if file_gets() >= 2:
                    break
                time.sleep(0.05)
            time.sleep(0.2)  # let the 2nd response finish slicing at serve time
            server.cfg["truncate_after"] = None

        threading.Thread(target=heal_after_second_get, daemon=True).start()
        with open(dest, "r+b") as fd:
            engine._download_single_segment(
                url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                stop_event=None, on_bytes=None, extra_headers=None,
                etag=None, meta_saver=None, max_retries=10,
            )
        assert seg.cursor > seg.end, "segment must be complete after retry"
        assert hashlib.sha256(open(dest, "rb").read()).hexdigest() == PAYLOAD_SHA256
        gets = [r for r in server.requests
                if r["method"] == "GET" and r["path"] == "/file.bin"]
        assert len(gets) >= 3, f"expected truncated attempts + a healing retry, saw {len(gets)} GETs"


class _BadContentRangeHandler(http.server.BaseHTTPRequestHandler):
    """Answers 206 but lies about Content-Range start (100 instead of 0)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAYLOAD[:1024]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes 100-1123/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def _serve_with(handler_cls):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    host, port = srv.server_address
    return srv, f"http://{host}:{port}/file.bin"


def test_206_content_range_start_mismatch_raises(tmp_path):
    """A 206 whose Content-Range start != requested pos is a ProtocolViolation."""
    srv, url = _serve_with(_BadContentRangeHandler)
    try:
        dest = str(tmp_path / "cr.bin")
        with pytest.raises(engine.ProtocolViolation):
            engine._fetch_chunk(url, 0, 511, dest)
    finally:
        srv.shutdown()
        srv.server_close()


def test_206_content_range_start_match_ok(tmp_path):
    """Happy path: correct Content-Range on a 206 completes the chunk."""
    with make_server({}) as server:
        dest = str(tmp_path / "ok.bin")
        n = engine._fetch_chunk(server.url("/file.bin"), 0, 4095, dest)
        assert n == 4096
        assert open(dest, "rb").read() == PAYLOAD[:4096]
