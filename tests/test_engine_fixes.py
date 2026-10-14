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
# Item 2: retry policy — 4xx (except 408/429) non-retryable, Retry-After honored,
# disk OSErrors (ENOSPC/EACCES) surface immediately
# ---------------------------------------------------------------------------

def _seg_download(url, tmp_path, name="t.bin", **kw):
    dest = str(tmp_path / name)
    engine.preallocate_file(dest, 4096)
    seg = engine.Segment(0, 0, 4095)
    lock = threading.Lock()
    kw.setdefault("max_retries", 2)
    with open(dest, "r+b") as fd:
        engine._download_single_segment(
            url=url, seg=seg, fd=fd, seg_lock=lock,
            stop_event=None, on_bytes=None, extra_headers=None,
            etag=None, meta_saver=None, **kw)
    return seg


def _get_count(server, path):
    return sum(1 for r in server.requests
               if r["method"] == "GET" and r["path"] == path)


def test_403_is_not_retried_and_surfaces_status(tmp_path):
    """403 (non-408/429 4xx) must fail fast: exactly one GET, status in error."""
    with make_server({"paths": {"/fb": {"status": 403}}}) as server:
        with pytest.raises(Exception, match="403"):
            _seg_download(server.url("/fb"), tmp_path)
        assert _get_count(server, "/fb") == 1, "403 must not be retried"


def test_404_is_not_retried(tmp_path):
    with make_server({"paths": {"/nf": {"status": 404}}}) as server:
        with pytest.raises(Exception, match="404"):
            _seg_download(server.url("/nf"), tmp_path)
        assert _get_count(server, "/nf") == 1, "404 must not be retried"


def test_429_honors_retry_after(tmp_path):
    """429 is retryable and must honor the Retry-After delay."""
    with make_server({"paths": {"/rl": {"status": 429, "retry_after": 2}}}) as server:
        t0 = time.time()
        with pytest.raises(Exception):
            _seg_download(server.url("/rl"), tmp_path, max_retries=1)
        dt = time.time() - t0
        assert _get_count(server, "/rl") == 2, "429 must be retried once"
        assert dt >= 2.0, f"Retry-After: 2 was not honored (waited {dt:.2f}s)"


def test_503_honors_retry_after(tmp_path):
    with make_server({"paths": {"/un": {"status": 503, "retry_after": 2}}}) as server:
        t0 = time.time()
        with pytest.raises(Exception):
            _seg_download(server.url("/un"), tmp_path, max_retries=1)
        dt = time.time() - t0
        assert _get_count(server, "/un") == 2, "503 must be retried once"
        assert dt >= 2.0, f"Retry-After: 2 was not honored (waited {dt:.2f}s)"


def test_408_is_retryable(tmp_path):
    with make_server({"paths": {"/to": {"status": 408}}}) as server:
        with pytest.raises(Exception):
            _seg_download(server.url("/to"), tmp_path, max_retries=1)
        assert _get_count(server, "/to") == 2, "408 must be retried"


def test_500_is_retryable(tmp_path):
    with make_server({"paths": {"/e500": {"status": 500}}}) as server:
        with pytest.raises(Exception):
            _seg_download(server.url("/e500"), tmp_path, max_retries=1)
        assert _get_count(server, "/e500") == 2, "500 must be retried"


class _EnospcFD:
    """File-like whose write() always fails with ENOSPC."""

    def __init__(self, f):
        self._f = f

    def seek(self, p):
        return self._f.seek(p)

    def write(self, b):
        raise OSError(28, "No space left on device")

    def flush(self):
        return self._f.flush()


def test_enospc_write_error_is_fatal_and_not_retried(tmp_path):
    """ENOSPC from fd.write must surface immediately (no retries)."""
    import errno
    with make_server({}) as server:
        dest = str(tmp_path / "disk.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 0, 4095)
        lock = threading.Lock()
        with open(dest, "r+b") as raw:
            with pytest.raises(OSError) as excinfo:
                engine._download_single_segment(
                    url=server.url("/file.bin"), seg=seg, fd=_EnospcFD(raw),
                    seg_lock=lock, stop_event=None, on_bytes=None,
                    extra_headers=None, etag=None, meta_saver=None,
                    max_retries=3)
        assert excinfo.value.errno == errno.ENOSPC
        assert _get_count(server, "/file.bin") == 1, "disk error must not be retried"


# ---------------------------------------------------------------------------
# Item 3: If-Range — strong ETag only; else Last-Modified; else omit.
# 200 on mid-file range -> RemoteFileChanged iff If-Range sent else ProtocolViolation.
# Resume falls back to Last-Modified when ETag is absent.
# ---------------------------------------------------------------------------

def _get_header(server, path, name):
    for r in server.requests:
        if r["method"] == "GET" and r["path"] == path:
            return r["headers"].get(name)
    return None


def test_if_range_sent_for_strong_etag(tmp_path):
    """Strong ETag -> If-Range carries the ETag."""
    with make_server({}) as server:
        dest = str(tmp_path / "s.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 0, 4095)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            engine._download_single_segment(
                url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                stop_event=None, on_bytes=None, extra_headers=None,
                etag='"0d4d-etag-1"', meta_saver=None, max_retries=2)
        assert _get_header(server, "/file.bin", "if-range") == '"0d4d-etag-1"'


def test_if_range_uses_last_modified_for_weak_etag(tmp_path):
    """Weak ETag (W/...) -> If-Range carries Last-Modified, not the weak ETag."""
    with make_server({"weak_etag": True}) as server:
        dest = str(tmp_path / "w.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 0, 4095)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            engine._download_single_segment(
                url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                stop_event=None, on_bytes=None, extra_headers=None,
                etag='W/"0d4d-etag-1"',
                last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
                meta_saver=None, max_retries=2)
        assert _get_header(server, "/file.bin", "if-range") == \
            "Wed, 01 Jan 2025 00:00:00 GMT"


def test_if_range_omitted_without_validators(tmp_path):
    """No ETag and no Last-Modified -> no If-Range header at all."""
    with make_server({}) as server:
        dest = str(tmp_path / "n.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 0, 4095)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            engine._download_single_segment(
                url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                stop_event=None, on_bytes=None, extra_headers=None,
                etag=None, last_modified=None, meta_saver=None, max_retries=2)
        assert _get_header(server, "/file.bin", "if-range") is None


def test_200_mid_file_with_if_range_raises_remote_file_changed(tmp_path):
    """200 on a mid-file range request with If-Range sent -> RemoteFileChanged."""
    with make_server({"ignore_range": True}) as server:
        dest = str(tmp_path / "c.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 100, 4095, cursor=100)  # pos > 0
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            with pytest.raises(engine.RemoteFileChanged):
                engine._download_single_segment(
                    url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                    stop_event=None, on_bytes=None, extra_headers=None,
                    etag='"0d4d-etag-1"', meta_saver=None, max_retries=1)


def test_200_mid_file_without_if_range_raises_protocol_violation(tmp_path):
    """200 on a mid-file range request with no If-Range -> ProtocolViolation."""
    with make_server({"ignore_range": True}) as server:
        dest = str(tmp_path / "p.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 100, 4095, cursor=100)  # pos > 0
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            with pytest.raises(engine.ProtocolViolation):
                engine._download_single_segment(
                    url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                    stop_event=None, on_bytes=None, extra_headers=None,
                    etag=None, last_modified=None, meta_saver=None,
                    max_retries=1)


def test_resume_falls_back_to_last_modified(tmp_path, monkeypatch):
    """Resume with no ETag anywhere: Last-Modified mismatch -> RemoteFileChanged."""
    with make_server({}) as server:
        url = server.url("/file.bin")
        dest = str(tmp_path / "lm.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, len(PAYLOAD) - 1, cursor=1024)
        engine.save_sidecar(dest, {
            "url": url, "total": len(PAYLOAD), "etag": None,
            "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
            "segments": [seg.to_dict()],
        })
        real_probe = engine.probe

        def fake_probe(u, extra_headers=None, proxy=None):
            m = real_probe(u, extra_headers=extra_headers, proxy=proxy)
            m["etag"] = None
            m["last_modified"] = "Thu, 02 Jan 2025 00:00:00 GMT"  # changed!
            return m

        monkeypatch.setattr(engine, "probe", fake_probe)
        with pytest.raises(engine.RemoteFileChanged, match="Last-Modified"):
            engine.download(url, dest=dest, conns=1)


def test_resume_last_modified_match_continues(tmp_path, monkeypatch):
    """Resume with no ETag and matching Last-Modified proceeds normally."""
    import hashlib
    with make_server({}) as server:
        url = server.url("/file.bin")
        dest = str(tmp_path / "lm2.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, len(PAYLOAD) - 1, cursor=0)
        engine.save_sidecar(dest, {
            "url": url, "total": len(PAYLOAD), "etag": None,
            "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
            "segments": [seg.to_dict()],
        })
        real_probe = engine.probe

        def fake_probe(u, extra_headers=None, proxy=None):
            m = real_probe(u, extra_headers=extra_headers, proxy=proxy)
            m["etag"] = None  # no ETag: resume must use Last-Modified
            return m

        monkeypatch.setattr(engine, "probe", fake_probe)
        res = engine.download(url, dest=dest, conns=1)
        assert res["sha256"] == PAYLOAD_SHA256
        # Worker must have sent If-Range with the Last-Modified value.
        assert _get_header(server, "/file.bin", "if-range") == \
            "Wed, 01 Jan 2025 00:00:00 GMT"


# ---------------------------------------------------------------------------
# Item 4: 200 on segment 0 is a full stream only for a single-segment plan;
# with several segments it is a ProtocolViolation. Full streams stop at total.
# ---------------------------------------------------------------------------

class _FirstSeg200Handler(http.server.BaseHTTPRequestHandler):
    """Returns 200 + full body for ranges starting at 0, proper 206 otherwise."""

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"strong-1"')
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        rng = self.headers.get("Range") or ""
        try:
            if rng.startswith("bytes=0-"):
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(PAYLOAD)
            else:
                spec = rng.split("=", 1)[1]
                a, b = spec.split("-", 1)
                s, e = int(a), int(b) if b else len(PAYLOAD) - 1
                data = PAYLOAD[s:e + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{e}/{len(PAYLOAD)}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def test_200_on_segment0_with_multi_segment_plan_raises(tmp_path):
    """Unit: 200 on seg 0 with a multi-segment plan -> ProtocolViolation."""
    with make_server({"ignore_range": True}) as server:
        dest = str(tmp_path / "m.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, 4095)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            with pytest.raises(engine.ProtocolViolation):
                engine._download_single_segment(
                    url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                    stop_event=None, on_bytes=None, extra_headers=None,
                    etag='"0d4d-etag-1"', meta_saver=None, max_retries=1,
                    single_segment=False, total=len(PAYLOAD))


def test_200_on_segment0_multi_segment_download_fails(tmp_path):
    """Integration: server 200s the seg-0 range while plan has 4 segments."""
    srv, url = _serve_with(_FirstSeg200Handler)
    try:
        dest = str(tmp_path / "mm.bin")
        with pytest.raises(engine.ProtocolViolation, match="multiple segments"):
            engine.download(url, dest=dest, conns=4)
    finally:
        srv.shutdown()
        srv.server_close()


def test_200_single_segment_full_stream_ok(tmp_path):
    """Unit: 200 on seg 0 with a single-segment plan is accepted as full stream."""
    import hashlib
    with make_server({"ignore_range": True}) as server:
        dest = str(tmp_path / "f.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, len(PAYLOAD) - 1)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            engine._download_single_segment(
                url=server.url("/file.bin"), seg=seg, fd=fd, seg_lock=lock,
                stop_event=None, on_bytes=None, extra_headers=None,
                etag=None, last_modified=None, meta_saver=None, max_retries=1,
                single_segment=True, total=len(PAYLOAD))
        assert seg.cursor == len(PAYLOAD)
        assert hashlib.sha256(open(dest, "rb").read()).hexdigest() == PAYLOAD_SHA256


class _OvershootHandler(http.server.BaseHTTPRequestHandler):
    """200 with MORE bytes than the known total (runaway stream)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAYLOAD + b"GARBAGE" * 4096
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def test_full_stream_stops_at_known_total(tmp_path):
    """A full stream with known total must stop at total, not read past it."""
    srv, url = _serve_with(_OvershootHandler)
    try:
        dest = str(tmp_path / "o.bin")
        engine.preallocate_file(dest, len(PAYLOAD))
        seg = engine.Segment(0, 0, len(PAYLOAD) - 1)
        lock = threading.Lock()
        with open(dest, "r+b") as fd:
            engine._download_single_segment(
                url=url, seg=seg, fd=fd, seg_lock=lock,
                stop_event=None, on_bytes=None, extra_headers=None,
                etag=None, last_modified=None, meta_saver=None, max_retries=1,
                single_segment=True, total=len(PAYLOAD))
        assert seg.cursor == len(PAYLOAD), \
            f"stream must stop at total, cursor={seg.cursor}"
        assert open(dest, "rb").read() == PAYLOAD
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# Item 5: fail fast — first fatal worker error sets a shared abort event so
# siblings stop; download() raises that original error (not a wrapper).
# ---------------------------------------------------------------------------

class _FailFastTrickleHandler(http.server.BaseHTTPRequestHandler):
    """Seg-0 range gets an instant 200 (fatal); other ranges trickle slowly."""

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"strong-1"')
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        rng = self.headers.get("Range") or ""
        try:
            if rng.startswith("bytes=0-"):
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(PAYLOAD)
            else:
                spec = rng.split("=", 1)[1]
                a, b = spec.split("-", 1)
                s, e = int(a), int(b) if b else len(PAYLOAD) - 1
                data = PAYLOAD[s:e + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{e}/{len(PAYLOAD)}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                for i in range(0, len(data), 65536):
                    self.wfile.write(data[i:i + 65536])
                    self.wfile.flush()
                    time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def test_fail_fast_first_error_aborts_siblings(tmp_path):
    """The seg-0 200 is fatal: siblings must stop at once and the original
    ProtocolViolation (not a wrapper) must propagate from download()."""
    srv, url = _serve_with(_FailFastTrickleHandler)
    try:
        dest = str(tmp_path / "ff.bin")
        t0 = time.time()
        with pytest.raises(engine.ProtocolViolation):
            engine.download(url, dest=dest, conns=4)
        dt = time.time() - t0
        # Siblings trickle ~16s to completion; abort must cut that short.
        assert dt < 8.0, f"siblings were not aborted promptly ({dt:.1f}s)"
    finally:
        srv.shutdown()
        srv.server_close()


def test_abort_event_interrupts_retry_backoff(tmp_path):
    """A worker sleeping in retry backoff must wake up when abort is set."""
    with make_server({"paths": {"/b503": {"status": 503, "retry_after": 30}}}) as server:
        dest = str(tmp_path / "ab.bin")
        engine.preallocate_file(dest, 4096)
        seg = engine.Segment(0, 0, 4095)
        lock = threading.Lock()
        abort = threading.Event()
        threading.Timer(0.5, abort.set).start()
        t0 = time.time()
        with open(dest, "r+b") as fd:
            with pytest.raises(engine._Aborted):
                engine._download_single_segment(
                    url=server.url("/b503"), seg=seg, fd=fd, seg_lock=lock,
                    stop_event=None, on_bytes=None, extra_headers=None,
                    etag=None, meta_saver=None, max_retries=5,
                    abort_event=abort)
        dt = time.time() - t0
        assert dt < 5.0, f"backoff was not interrupted ({dt:.1f}s)"


# ---------------------------------------------------------------------------
