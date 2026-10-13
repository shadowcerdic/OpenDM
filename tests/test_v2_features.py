"""Tests for OpenDM v2.0 Architecture Upgrades.

Covers:
- Test 1: Pre-allocated parallel file writing and crash-resume recovery via sidecar state file.
- Test 2: Dynamic In-Flight Work Stealing (fast worker steals remaining range of slow worker).
- Test 3: Mid-segment 200 OK guard (immediate abort on mid-file 200 response to prevent corruption).
- Test 4: ETag / If-Range mismatch on resume (detection of modified remote files).
- Test 5: RFC 5987 Content-Disposition parsing during pre-flight check.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import socketserver
import threading
import time
from pathlib import Path

import pytest
from opendm import engine
from opendm.engine import (
    Segment,
    download,
    load_sidecar,
    preallocate_file,
    steal_work,
    ProtocolViolation,
    RemoteFileChanged,
)
from opendm.sanitize import parse_content_disposition


# -----------------------------------------------------------------------------
# Test HTTP Servers
# -----------------------------------------------------------------------------
class CustomRangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    etag = '"v2-original"'
    respond_200_on_range = False

    def log_message(self, format, *args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", self.etag)
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if_range = self.headers.get("If-Range")

        # Check If-Range mismatch
        if if_range and if_range != self.etag:
            # File modified! Return 200 with full content or 412
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.send_header("ETag", self.etag)
            self.end_headers()
            self.wfile.write(self.payload)
            return

        if rng and not self.respond_200_on_range:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else len(self.payload) - 1
                e = min(e, len(self.payload) - 1)
                data = self.payload[s : e + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{e}/{len(self.payload)}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("ETag", self.etag)
                self.end_headers()
                self.wfile.write(data)
                return

        # Server improperly returns 200 OK for Range request
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("ETag", self.etag)
        self.end_headers()
        self.wfile.write(self.payload)


class SlowAndFastHandler(http.server.BaseHTTPRequestHandler):
    """Throttles requests for first half, fast for second half to test work stealing."""
    payload = b""

    def log_message(self, format, *args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        s, e = 0, len(self.payload) - 1
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else len(self.payload) - 1

        self.send_response(206)
        self.send_header("Content-Range", f"bytes {s}-{e}/{len(self.payload)}")
        self.send_header("Content-Length", str(e - s + 1))
        self.end_headers()

        data = self.payload[s : e + 1]
        # If worker is serving lower half, artificially delay stream in chunks
        is_slow = (s < len(self.payload) // 2)
        chunk_sz = 65536
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_sz]
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            offset += len(chunk)
            if is_slow:
                time.sleep(0.02)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------
def test_preallocated_parallel_write_and_resume(tmp_path):
    """Test 1: Pre-allocated parallel file writing and crash-resume recovery via sidecar state file."""
    data = os.urandom(8 * 1024 * 1024)  # 8 MB payload
    CustomRangeHandler.payload = data
    CustomRangeHandler.etag = '"tag-initial"'
    CustomRangeHandler.respond_200_on_range = False

    server = socketserver.TCPServer(("127.0.0.1", 0), CustomRangeHandler)
    port = server.server_address[1]
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    url = f"http://127.0.0.1:{port}/testfile.dat"
    dest = str(tmp_path / "testfile.dat")

    # Start download throttled so pause can reliably hit mid-transfer
    stop = threading.Event()

    def stop_later():
        time.sleep(0.05)
        stop.set()

    threading.Thread(target=stop_later, daemon=True).start()

    try:
        download(url, dest=dest, conns=4, stop_event=stop, speed_limit=512 * 1024)
    except engine.Paused:
        pass

    # Verify sidecar state file exists and file is preallocated
    sidecar = load_sidecar(dest)
    assert sidecar is not None, "Sidecar state file must exist after pause"
    assert sidecar["total"] == len(data)
    assert os.path.exists(dest)
    assert os.path.getsize(dest) == len(data)

    # Resume download cleanly without speed limit
    res = download(url, dest=dest, conns=4)
    server.shutdown()

    assert res["sha256"] == hashlib.sha256(data).hexdigest()
    assert not os.path.exists(f"{dest}.opendm"), "Sidecar file must be cleaned up after verified completion"


def test_dynamic_work_stealing():
    """Test 2: Dynamic In-Flight Work Stealing logic with PAGE_SIZE alignment."""
    lock = threading.Lock()
    # Create 2 segments: seg 0 has large remaining work (6 MB), seg 1 finished
    seg0 = Segment(0, 0, 6 * 1024 * 1024 - 1, cursor=0)
    seg1 = Segment(1, 6 * 1024 * 1024, 7 * 1024 * 1024 - 1, cursor=7 * 1024 * 1024)
    segments = [seg0, seg1]

    # Free worker calls steal_work
    stolen = steal_work(segments, lock)
    assert stolen is not None, "Worker should successfully steal work from slowest segment"
    assert len(segments) == 3

    # Check boundaries
    assert seg0.end < stolen.start
    assert stolen.start % engine.PAGE_SIZE == 0, "Stolen segment start must align to PAGE_SIZE (4096 bytes)"
    assert stolen.end == 6 * 1024 * 1024 - 1
    assert stolen.cursor == stolen.start
    assert seg0.end == stolen.start - 1


def test_mid_segment_200_ok_corruption_guard(tmp_path):
    """Test 3: Mid-segment 200 OK guard - immediate abort when server responds with 200 OK to mid-file Range."""
    data = os.urandom(4 * 1024 * 1024)
    CustomRangeHandler.payload = data
    CustomRangeHandler.respond_200_on_range = True  # Violates Range protocol

    server = socketserver.TCPServer(("127.0.0.1", 0), CustomRangeHandler)
    port = server.server_address[1]
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    url = f"http://127.0.0.1:{port}/corrupt_test.dat"
    dest = str(tmp_path / "corrupt_test.dat")

    # With multiple connections, workers for start > 0 will receive 200 OK and abort
    with pytest.raises(Exception) as excinfo:
        download(url, dest=dest, conns=4)

    server.shutdown()
    assert "206" in str(excinfo.value) or "ProtocolViolation" in str(excinfo.value) or "Download workers failed" in str(excinfo.value)


def test_etag_if_range_mismatch_on_resume(tmp_path):
    """Test 4: ETag / If-Range mismatch on Resume - detect remote file changes."""
    data_v1 = b"A" * (4 * 1024 * 1024)
    data_v2 = b"B" * (4 * 1024 * 1024)

    CustomRangeHandler.payload = data_v1
    CustomRangeHandler.etag = '"v1-hash"'
    CustomRangeHandler.respond_200_on_range = False

    server = socketserver.TCPServer(("127.0.0.1", 0), CustomRangeHandler)
    port = server.server_address[1]
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    url = f"http://127.0.0.1:{port}/etag_test.dat"
    dest = str(tmp_path / "etag_test.dat")

    # Pause download throttled
    stop = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.05), stop.set()), daemon=True).start()
    try:
        download(url, dest=dest, conns=2, stop_event=stop, speed_limit=256 * 1024)
    except engine.Paused:
        pass

    # Remote file changes before resume!
    CustomRangeHandler.payload = data_v2
    CustomRangeHandler.etag = '"v2-new-hash"'

    with pytest.raises(Exception) as excinfo:
        download(url, dest=dest, conns=2)

    server.shutdown()
    assert "RemoteFileChanged" in str(excinfo.value) or "mismatch" in str(excinfo.value) or "changed" in str(excinfo.value)


def test_rfc5987_content_disposition_preflight():
    """Test 5: RFC 5987 Content-Disposition (filename*=UTF-8''...) parsing during Pre-Flight."""
    # Persian Unicode encoded filename
    raw_header = "attachment; filename*=UTF-8''%D8%AF%D8%A7%D9%86%D9%84%D9%88%D8%AF%20%D9%81%D8%A7%DB%8C%D9%84.zip"
    parsed = parse_content_disposition(raw_header)
    assert parsed == "دانلود فایل.zip"

    # Fallback to standard filename
    raw_header_ascii = 'attachment; filename="setup_v2.0.exe"'
    parsed_ascii = parse_content_disposition(raw_header_ascii)
    assert parsed_ascii == "setup_v2.0.exe"

    # RFC 5987 takes precedence over legacy filename
    raw_combo = 'attachment; filename="fallback.zip"; filename*=UTF-8\'\'%D9%BE%D8%B1%D9%88%DA%98%D9%87.zip'
    parsed_combo = parse_content_disposition(raw_combo)
    assert parsed_combo == "پروژه.zip"
