"""OpenDM v2.0 high-performance multi-connection download engine.

Features:
- Pre-allocation & lock-free parallel disk I/O (no .part files, no linear assemble())
- Thread-safe direct seeks into target file
- Atomic sidecar metadata (<filename>.opendm) for segment tracking, ETag, and Last-Modified
- Dynamic in-flight segment splitting (Work Stealing) with PAGE_SIZE alignment
- Protocol safety: mid-segment 200 OK corruption guard & If-Range resume validation
"""
from __future__ import annotations

import collections
import email.utils
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from .sanitize import parse_content_disposition, sanitize_filename

UA = "OpenDM/2.0"
PART_DIR = ".opendm"
MIN_SPLIT_SIZE = 2 * 1024 * 1024  # 2 MB minimum remaining for work stealing
PAGE_SIZE = 4096
MAX_CONNS = 16  # hard cap on parallel connections/workers


class Paused(Exception):
    """Raised inside workers when download is paused/stopped."""


class RemoteFileChanged(Exception):
    """Raised when remote file changed (ETag/If-Range mismatch) on resume."""


class ProtocolViolation(Exception):
    """Raised when server violates HTTP range protocol (e.g. 200 OK on mid-file request)."""


class ShortReadError(Exception):
    """Raised when a response ends before the requested range is complete.

    Retryable: the worker re-requests from the current cursor with backoff.
    """


class _DiskError(OSError):
    """Non-retryable disk failure from fd.seek/fd.write (ENOSPC, EACCES, ...).

    Never retried: retrying a full or unwritable disk cannot succeed.
    """


class _Aborted(Exception):
    """Internal: a sibling worker hit a fatal error; stop quietly.

    The shared abort event is set by the first fatal worker error; other
    workers raise this (instead of doing more work) and download() re-raises
    the original error.
    """


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value into seconds, or None if unusable."""
    if not value:
        return None
    value = value.strip()
    try:
        secs = float(value)
        return min(max(secs, 0.0), 120.0)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return min(max(delta, 0.0), 120.0)
    except Exception:
        return None


def _interruptible_sleep(stop_event: threading.Event | None,
                         abort_event: threading.Event | None,
                         seconds: float):
    """Sleep, raising Paused/_Aborted promptly if stop/abort is set."""
    t_end = time.time() + max(0.0, seconds)
    while time.time() < t_end:
        if stop_event is not None and stop_event.is_set():
            raise Paused()
        if abort_event is not None and abort_event.is_set():
            raise _Aborted()
        time.sleep(min(0.2, t_end - time.time()))


class TokenBucket:
    """Thread-safe token-bucket rate limiter with a live-adjustable limit.

    Tokens accrue at ``bytes_per_sec`` (no initial burst); consume(n) blocks
    until n tokens are available. A limit of 0 (or negative) means unlimited.
    Shared across worker threads: pass one instance to download() as
    ``rate_limiter``.
    """

    def __init__(self, bytes_per_sec: float = 0):
        self._lock = threading.Lock()
        self._limit = 0.0
        self._tokens = 0.0
        self._last = time.monotonic()
        self.set_limit(bytes_per_sec)

    def set_limit(self, bytes_per_sec: float):
        """Change the rate limit live (bytes/sec; 0 = unlimited)."""
        with self._lock:
            self._refill()
            self._limit = max(0.0, float(bytes_per_sec or 0))

    def consume(self, n: int):
        """Block until n bytes may be transferred."""
        n = int(n)
        if n <= 0:
            return
        while n > 0:
            with self._lock:
                self._refill()
                if self._limit <= 0:
                    return
                # Take whatever is available; large requests are satisfied
                # in pieces so n > burst cap can never deadlock.
                take = min(n, self._tokens)
                if take > 0:
                    self._tokens -= take
                    n -= take
                    if n <= 0:
                        return
                wait = max(n, 1) / self._limit
            # Sleep outside the lock in small slices so set_limit() and
            # pause/abort paths stay responsive.
            time.sleep(min(wait, 0.05))

    def _refill(self):
        now = time.monotonic()
        if self._limit > 0:
            # Cap accumulated burst at one second of tokens.
            self._tokens = min(self._limit,
                               self._tokens + (now - self._last) * self._limit)
        self._last = now


class _SlidingRate:
    """Sliding-window byte rate over the last ~window seconds."""

    def __init__(self, window: float = 3.0):
        self._window = window
        self._samples: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def add(self, n: int):
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, n))
            self._evict(now)

    def rate(self) -> float:
        now = time.monotonic()
        with self._lock:
            self._evict(now)
            total = sum(b for _, b in self._samples)
        return total / self._window

    def _evict(self, now: float):
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


class Segment:
    """Represents a range chunk [start, end] with dynamic cursor and boundary."""

    def __init__(self, seg_id: int, start: int, end: int, cursor: int | None = None):
        self.seg_id = seg_id
        self.start = start
        self.end = end
        self.cursor = start if cursor is None else cursor

    @property
    def remaining(self) -> int:
        return max(0, self.end - self.cursor + 1)

    @property
    def have(self) -> int:
        return max(0, self.cursor - self.start)

    def to_dict(self) -> dict:
        return {
            "id": self.seg_id,
            "start": self.start,
            "cursor": self.cursor,
            "end": self.end,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Segment:
        return cls(d["id"], d["start"], d["end"], d.get("cursor", d["start"]))


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that never leaks credentials cross-origin.

    - Only http/https redirect targets are followed; anything else raises.
    - When the redirect target differs from the request URL in scheme, host
      or port, the Cookie and Authorization headers are stripped.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        target = urllib.parse.urlparse(absolute)
        if target.scheme not in ("http", "https"):
            raise urllib.error.HTTPError(
                absolute, code,
                f"Blocked redirect to non-http(s) URL: {absolute!r}",
                headers, fp,
            )
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        orig = urllib.parse.urlparse(req.full_url)
        if ((target.scheme, target.hostname, target.port)
                != (orig.scheme, orig.hostname, orig.port)):
            for name in ("Cookie", "Authorization"):
                if new_req.has_header(name):
                    new_req.remove_header(name)
                new_req.unredirected_hdrs.pop(name, None)
        return new_req


def probe(url: str, extra_headers: dict | None = None, proxy: str | None = None) -> dict:
    """Probe server with HEAD request (falls back to Range: bytes=0-1). Supports HTTP/SOCKS5 proxy."""
    hdrs = {"User-Agent": UA}
    if extra_headers:
        for k, v in extra_headers.items():
            if k.lower() in ("cookie", "referer", "user-agent", "authorization"):
                hdrs[k] = v

    handlers = [SafeRedirectHandler()]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)

    req = urllib.request.Request(url, method="HEAD", headers=hdrs)
    size = 0
    arange = ""
    cd_name = None
    etag = None
    last_modified = None
    try:
        with opener.open(req, timeout=20) as r:
            size = int(r.headers.get("Content-Length") or 0)
            arange = (r.headers.get("Accept-Ranges") or "").lower()
            etag = r.headers.get("ETag")
            last_modified = r.headers.get("Last-Modified")
            cd = r.headers.get("Content-Disposition")
            if cd:
                cd_name = parse_content_disposition(cd)
    except Exception:
        pass

    supports = size > 0 and ("bytes" in arange)
    if not supports:
        probe_hdrs = dict(hdrs)
        probe_hdrs["Range"] = "bytes=0-1"
        req = urllib.request.Request(url, headers=probe_hdrs)
        try:
            with opener.open(req, timeout=20) as r:
                supports = r.status == 206 and r.headers.get("Content-Range") is not None
                if not size:
                    cr = r.headers.get("Content-Range") or ""
                    m = re.search(r"/(\d+)", cr)
                    if m:
                        size = int(m.group(1))
                if not etag:
                    etag = r.headers.get("ETag")
                if not last_modified:
                    last_modified = r.headers.get("Last-Modified")
                if not cd_name and r.headers.get("Content-Disposition"):
                    cd_name = parse_content_disposition(r.headers.get("Content-Disposition"))
        except Exception:
            supports = False

    return {
        "size": size,
        "ranges": bool(supports),
        "content_disposition_filename": cd_name,
        "etag": etag,
        "last_modified": last_modified,
    }


def plan_ranges(total: int, n: int) -> list[tuple[int, int]]:
    """Contiguous split of [0, total-1] into up to n page-aligned segments."""
    if n <= 1 or total <= 0:
        return [(0, max(total - 1, 0))]
    seg = (total + n - 1) // n
    seg = ((seg + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE
    out, s = [], 0
    while s < total:
        e = min(s + seg - 1, total - 1)
        out.append((s, e))
        s = e + 1
    return out


def get_sidecar_path(target_path: str) -> str:
    """Return path to sidecar metadata file."""
    return f"{target_path}.opendm"


def part_path(final_path: str) -> str:
    """Return the in-progress partial-file path for a final destination.

    download() writes to this path and atomically renames it to the final
    name (os.replace) only after the completion gate passes.
    """
    return f"{final_path}.opendm.part"


# Serializes sidecar writes process-wide. Concurrent os.replace() calls to
# the same destination are unsafe on Windows: the replace can race with file
# filter drivers (e.g. antivirus real-time scanning on CI runners) that briefly
# hold the destination open, surfacing as PermissionError [WinError 5].
_sidecar_lock = threading.Lock()


def _replace_with_retry(src: str, dst: str) -> None:
    """os.replace with retries on transient Windows file locks.

    File filter drivers (antivirus, search indexer) can briefly lock a freshly
    written file; retrying a few times with backoff is the standard mitigation.
    A persistent PermissionError still raises after the retries.
    """
    last: BaseException | None = None
    for attempt in range(4):
        try:
            os.replace(src, dst)
            return
        except PermissionError as ex:
            last = ex
            time.sleep(0.05 * (2 ** attempt))
    assert last is not None
    raise last


def save_sidecar(target_path: str, data: dict):
    """Atomically write sidecar metadata via a unique temp file.

    The temp name is unique per call so concurrent checkpoints (timer thread
    and workers) cannot clobber each other's temp file; os.replace keeps the
    visible sidecar update atomic (last writer wins). Writes are serialized
    by _sidecar_lock so only one replace is ever in flight.
    """
    path = get_sidecar_path(target_path)
    directory = os.path.dirname(os.path.abspath(path))
    with _sidecar_lock:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".opendm-",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            _replace_with_retry(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def load_sidecar(target_path: str) -> dict | None:
    """Load sidecar metadata if available."""
    path = get_sidecar_path(target_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def steal_work(segments: list[Segment], lock: threading.Lock,
               exclude_ids: set[int] | None = None) -> Segment | None:
    """Find active segment with largest remaining bytes and steal its second half.

    Splits range aligned to PAGE_SIZE. Returns newly assigned Segment or None.
    Segments whose id() is in exclude_ids (e.g. waiting in the pending queue)
    are never stolen from.
    """
    with lock:
        active_candidates = [
            s for s in segments
            if (s.end - s.cursor + 1) >= MIN_SPLIT_SIZE
            and (exclude_ids is None or id(s) not in exclude_ids)
        ]
        if not active_candidates:
            return None

        slowest = max(active_candidates, key=lambda s: s.end - s.cursor + 1)
        rem = slowest.end - slowest.cursor + 1
        half = rem // 2
        # Align mid to PAGE_SIZE
        mid = slowest.cursor + half
        mid = (mid // PAGE_SIZE) * PAGE_SIZE

        if mid <= slowest.cursor or mid >= slowest.end:
            return None

        old_end = slowest.end
        slowest.end = mid - 1

        new_seg_id = len(segments)
        stolen = Segment(new_seg_id, mid, old_end, cursor=mid)
        segments.append(stolen)
        return stolen


def preallocate_file(filepath: str, size: int):
    """Pre-allocate file on disk to exact size if it doesn't exist or differs.

    Checks free disk space with shutil.disk_usage before growing the file and
    raises a clear OSError when there is not enough room.
    """
    parent = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(parent, exist_ok=True)
    existing = os.path.getsize(filepath) if os.path.exists(filepath) else 0
    if existing == size:
        return
    need = max(0, size - existing)
    if need > 0:
        free = shutil.disk_usage(parent).free
        if free < need:
            raise OSError(
                errno.ENOSPC,
                f"Insufficient disk space for {filepath}: need {need} bytes, "
                f"only {free} bytes free on {parent}")
    with open(filepath, "a+b") as f:
        if size > 0:
            f.truncate(size)


def _if_range_value(etag: str | None, last_modified: str | None) -> str | None:
    """Pick the If-Range validator: strong ETag preferred, else Last-Modified.

    A weak ETag (starting with ``W/``) is not a valid If-Range validator, so
    Last-Modified is used instead. Returns None when neither is available.
    """
    if etag and not etag.startswith("W/"):
        return etag
    if last_modified:
        return last_modified
    return None


def _worker_loop(
    url: str,
    seg: Segment,
    dest_path: str,
    segments: list[Segment],
    seg_lock: threading.Lock,
    stop_event: threading.Event | None,
    on_bytes: Callable[[int], None] | None,
    extra_headers: dict | None,
    etag: str | None,
    meta_saver: Callable[[], None] | None,
    proxy: str | None = None,
    max_retries: int = 5,
    last_modified: str | None = None,
    single_segment: bool = True,
    total: int = 0,
    abort_event: threading.Event | None = None,
    fd_registry: tuple | None = None,
    pending: collections.deque | None = None,
):
    """Worker handling segment fetching and dynamic work stealing."""
    fd = open(dest_path, "r+b")
    if fd_registry is not None:
        _fds, _fds_lock = fd_registry
        with _fds_lock:
            _fds.append(fd)
    try:
        current_seg = seg
        while current_seg is not None:
            if stop_event is not None and stop_event.is_set():
                raise Paused()
            if abort_event is not None and abort_event.is_set():
                raise _Aborted()

            _download_single_segment(
                url=url,
                seg=current_seg,
                fd=fd,
                seg_lock=seg_lock,
                stop_event=stop_event,
                on_bytes=on_bytes,
                extra_headers=extra_headers,
                etag=etag,
                last_modified=last_modified,
                meta_saver=meta_saver,
                proxy=proxy,
                max_retries=max_retries,
                single_segment=single_segment,
                total=total,
                abort_event=abort_event,
            )

            # Segment finished. Pick up a pending segment first, if any;
            # only then attempt work stealing.
            if stop_event is not None and stop_event.is_set():
                raise Paused()
            if abort_event is not None and abort_event.is_set():
                raise _Aborted()
            current_seg = None
            exclude_ids = None
            if pending is not None:
                with seg_lock:
                    if pending:
                        current_seg = pending.popleft()
                    exclude_ids = {id(s) for s in pending}
            if current_seg is None:
                current_seg = steal_work(segments, seg_lock,
                                         exclude_ids=exclude_ids)
            if current_seg is not None and meta_saver:
                meta_saver()
    finally:
        if fd_registry is not None:
            _fds, _fds_lock = fd_registry
            with _fds_lock:
                try:
                    _fds.remove(fd)
                except ValueError:
                    pass
        fd.close()


def _download_single_segment(
    url: str,
    seg: Segment,
    fd,
    seg_lock: threading.Lock,
    stop_event: threading.Event | None,
    on_bytes: Callable[[int], None] | None,
    extra_headers: dict | None,
    etag: str | None,
    meta_saver: Callable[[], None] | None,
    proxy: str | None = None,
    max_retries: int = 5,
    last_modified: str | None = None,
    single_segment: bool = True,
    total: int = 0,
    abort_event: threading.Event | None = None,
):
    """Fetch assigned segment range directly writing to preallocated fd."""
    handlers = [SafeRedirectHandler()]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)

    # If-Range validator: strong ETag only; weak ETag falls back to
    # Last-Modified; omitted when neither is available.
    if_range = _if_range_value(etag, last_modified)

    attempt = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            raise Paused()
        if abort_event is not None and abort_event.is_set():
            raise _Aborted()

        with seg_lock:
            pos = seg.cursor
            current_end = seg.end

        if pos > current_end:
            return  # Completed this segment

        hdrs = {"User-Agent": UA, "Range": f"bytes={pos}-{current_end}"}
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() in ("cookie", "referer", "user-agent", "authorization"):
                    hdrs[k] = v
        if if_range:
            hdrs["If-Range"] = if_range

        try:
            req = urllib.request.Request(url, headers=hdrs)
            with opener.open(req, timeout=30) as r:
                # A 200 on a mid-file range request means the server ignored
                # Range. If we sent If-Range, the file changed remotely;
                # without a validator it is a plain protocol violation.
                if pos > 0 and r.status == 200:
                    if if_range:
                        raise RemoteFileChanged(
                            "Server returned 200 OK on range request "
                            f"bytes={pos}-{current_end} with If-Range; "
                            "remote file changed."
                        )
                    raise ProtocolViolation(
                        f"Server responded with 200 instead of 206 Partial Content "
                        f"for mid-segment request bytes={pos}-{current_end} "
                        "(no If-Range validator was sent)"
                    )
                # Protocol Safety: Guard against server ignoring Range on mid-file request
                if pos > 0 and r.status != 206:
                    raise ProtocolViolation(
                        f"Server responded with {r.status} instead of 206 Partial Content "
                        f"for mid-segment request bytes={pos}-{current_end}"
                    )

                # Content-Range integrity: a 206 must start where we asked.
                if r.status == 206:
                    cr = r.headers.get("Content-Range") or ""
                    m = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", cr)
                    if not m or int(m.group(1)) != pos:
                        raise ProtocolViolation(
                            f"Content-Range start mismatch: requested bytes={pos}-{current_end}, "
                            f"server answered {cr!r}"
                        )

                # A 200 on segment 0 is a full stream only when the plan has
                # exactly one segment; otherwise the server is ignoring Range
                # and the stream would overwrite sibling segments.
                is_full_stream = (r.status == 200 and pos == 0)
                if is_full_stream and not single_segment:
                    raise ProtocolViolation(
                        f"Server returned 200 OK for range request "
                        f"bytes={pos}-{current_end} but the plan has multiple "
                        f"segments; refusing to overwrite sibling segments"
                    )

                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise Paused()
                    if abort_event is not None and abort_event.is_set():
                        raise _Aborted()

                    with seg_lock:
                        current_end = seg.end
                        curr_pos = seg.cursor

                    # A full stream with known total stops at total.
                    eff_end = current_end
                    if is_full_stream and total > 0:
                        eff_end = min(current_end, total - 1)

                    if curr_pos > eff_end:
                        break  # Range complete (or segment stolen/shortened)

                    chunk_limit = min(65536, eff_end - curr_pos + 1)
                    buf = r.read(chunk_limit)
                    if not buf:
                        if not is_full_stream:
                            with seg_lock:
                                cursor_now = seg.cursor
                                end_now = seg.end
                            if cursor_now <= end_now:
                                raise ShortReadError(
                                    f"short read: connection ended after "
                                    f"{cursor_now - pos} of {end_now - pos + 1} bytes "
                                    f"for range bytes={pos}-{end_now}"
                                )
                        break

                    # Thread-safe write to preallocated file.
                    # Disk failures (full disk, permissions) are fatal: never retry.
                    try:
                        fd.seek(curr_pos)
                        fd.write(buf)
                    except OSError as ex:
                        if ex.errno in (errno.ENOSPC, errno.EACCES,
                                        errno.EDQUOT, errno.EROFS):
                            raise _DiskError(
                                ex.errno,
                                f"Disk write failed at offset {curr_pos}: "
                                f"{os.strerror(ex.errno)}; download aborted"
                            ) from ex
                        raise

                    with seg_lock:
                        seg.cursor += len(buf)

                    if on_bytes:
                        on_bytes(len(buf))

            if meta_saver:
                meta_saver()
            return
        except Paused:
            raise
        except _Aborted:
            raise
        except _DiskError:
            raise
        except (ProtocolViolation, RemoteFileChanged):
            raise
        except urllib.error.HTTPError as ex:
            status = ex.code or 0
            if 400 <= status < 500 and status not in (408, 429):
                # Non-retryable client error: surface the HTTP status as-is.
                raise
            # Retryable: 408/429/5xx. Honor Retry-After for 429/503.
            wait = None
            if status in (429, 503):
                wait = _parse_retry_after(
                    ex.headers.get("Retry-After") if ex.headers else None)
            attempt += 1
            if attempt > max_retries or (stop_event is not None and stop_event.is_set()):
                raise
            _interruptible_sleep(
                stop_event, abort_event,
                wait if wait is not None else min(2 ** attempt * 0.5, 5.0))
        except OSError as ex:
            attempt += 1
            if attempt > max_retries or (stop_event is not None and stop_event.is_set()):
                raise RuntimeError(f"Network error after {attempt} attempts: {ex}")
            _interruptible_sleep(stop_event, abort_event, min(2 ** attempt * 0.5, 5.0))
        except Exception as ex:
            attempt += 1
            if attempt > max_retries or (stop_event is not None and stop_event.is_set()):
                raise RuntimeError(f"Network error after {attempt} attempts: {ex}")
            _interruptible_sleep(stop_event, abort_event, min(2 ** attempt * 0.5, 5.0))


def compute_sha256(filepath: str, total_size: int = -1) -> str:
    """Compute sha256 checksum in chunks."""
    h = hashlib.sha256()
    bytes_read = 0
    with open(filepath, "rb") as f:
        while True:
            limit = min(262144, total_size - bytes_read) if total_size > 0 else 262144
            if limit <= 0 and total_size > 0:
                break
            buf = f.read(limit)
            if not buf:
                break
            h.update(buf)
            bytes_read += len(buf)
    return h.hexdigest()


# Backwards compatibility stub for external callers/tests
def assemble(dest: str, total: int, parts: list[str]) -> str:
    """Backward compatibility helper (deprecated in v2.0)."""
    return compute_sha256(dest, total)


def _fetch_chunk(url: str, start: int, end: int, part_path: str,
                 stop_event=None, on_bytes=None, extra_headers: dict | None = None,
                 max_retries: int = 5) -> int:
    """Legacy test helper: fetches [start, end] into file with seek/truncate support."""
    preallocate_file(part_path, max(end + 1, 0))
    seg = Segment(0, start, end, cursor=start)
    lock = threading.Lock()
    with open(part_path, "r+b") as fd:
        _download_single_segment(
            url=url, seg=seg, fd=fd, seg_lock=lock,
            stop_event=stop_event, on_bytes=on_bytes,
            extra_headers=extra_headers, etag=None, last_modified=None,
            meta_saver=None, max_retries=max_retries
        )
    return seg.have


def write_zone_identifier(final_path: str, url: str,
                            referer: str | None = None):
    """Write the Windows Mark-of-the-Web Zone.Identifier ADS.

    Creates the NTFS alternate data stream ``<final_path>:Zone.Identifier``
    with ``[ZoneTransfer]`` / ``ZoneId=3`` (internet zone), a ``ReferrerUrl``
    line when a referer is known, and a ``HostUrl`` line carrying the download
    URL with any userinfo stripped -- mirroring what browsers write for
    internet downloads.

    Best effort: any OSError (non-NTFS filesystem, permissions, ...) is
    swallowed. No-op on non-Windows platforms.

    NOTE -- UNVERIFIED ON REAL WINDOWS: implemented and unit-tested against
    mocks only; needs a real Windows/NTFS run to confirm ADS behavior.
    """
    if sys.platform != "win32":
        return
    parsed = urllib.parse.urlsplit(url)
    # Strip userinfo (https://user:pass@host/ -> https://host/), keeping
    # host case, port, path, query and fragment intact.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    host_url = urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    lines = ["[ZoneTransfer]", "ZoneId=3"]
    if referer:
        lines.append(f"ReferrerUrl={referer}")
    lines.append(f"HostUrl={host_url}")
    content = "\n".join(lines) + "\n"
    try:
        with open(f"{final_path}:Zone.Identifier", "w",
                  encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass


def download(
    url: str,
    dest: str | None = None,
    conns: int = 4,
    status_cb: Callable[[int, int, float], None] | None = None,
    state_cb: Callable[[str], None] | None = None,
    parts_cb: Callable[[list[dict]], None] | None = None,
    stop_event: threading.Event | None = None,
    speed_limit: int = 0,
    headers: dict | None = None,
    proxy: str | None = None,
    rate_limiter: TokenBucket | None = None,
    rate_cb: Callable[[float], None] | None = None,
) -> dict:
    """v2.0 Pre-allocated, lock-free parallel download with Work Stealing."""
    conns = max(1, min(int(conns), MAX_CONNS))
    t0 = time.time()
    meta = probe(url, extra_headers=headers, proxy=proxy)
    total = meta["size"]
    etag = meta.get("etag")
    last_modified = meta.get("last_modified")

    if not dest:
        cand = meta.get("content_disposition_filename")
        if not cand:
            path_part = urllib.parse.urlparse(url).path
            cand = os.path.basename(re.sub(r"[?#].*$", "", path_part)) or "download.bin"
        name = sanitize_filename(cand)
    else:
        name = dest

    # Partial-file safety: all bytes go to <final>.opendm.part; the final
    # name appears only after the completion gate passes (os.replace).
    part = part_path(name)

    # Pre-allocation on the .part file (with a free-space check).
    preallocate_file(part, total)

    # Legacy compatibility: ensure PART_DIR exists if checked
    workdir = os.path.join(os.path.dirname(os.path.abspath(name)), PART_DIR)
    os.makedirs(workdir, exist_ok=True)

    # Load existing sidecar metadata if available
    sidecar = load_sidecar(name)
    seg_lock = threading.Lock()

    # Resume verification & segment loading (requires the .part file too).
    segments: list[Segment] = []
    if (sidecar and sidecar.get("url") == url
            and sidecar.get("total") == total
            and os.path.exists(part)):
        # Resume validation: strong ETag first, falling back to Last-Modified
        # when the ETag is absent on either side.
        saved_etag = sidecar.get("etag")
        saved_lm = sidecar.get("last_modified")
        if etag and saved_etag:
            if etag != saved_etag:
                raise RemoteFileChanged(f"ETag mismatch on resume: {etag} != {saved_etag}")
        elif last_modified and saved_lm and last_modified != saved_lm:
            raise RemoteFileChanged(
                f"Last-Modified mismatch on resume: {last_modified} != {saved_lm}")
        for s_dict in sidecar.get("segments", []):
            segments.append(Segment.from_dict(s_dict))
    else:
        # Fresh download plan
        if meta["ranges"] and total > 0:
            ranges = plan_ranges(total, conns)
            for i, (s, e) in enumerate(ranges):
                segments.append(Segment(i, s, e, cursor=s))
        else:
            segments.append(Segment(0, 0, max(total - 1, 0), cursor=0))

    def save_meta():
        with seg_lock:
            data = {
                "url": url,
                "total": total,
                "etag": etag,
                "last_modified": last_modified,
                "segments": [s.to_dict() for s in segments],
            }
        save_sidecar(name, data)

    # Registry of live worker fds so checkpoints can fsync them.
    worker_fds: list = []
    worker_fds_lock = threading.Lock()

    def checkpoint():
        """Flush + fsync every worker fd, then persist the sidecar.

        The sidecar never records bytes that are not yet flushed to disk.
        """
        with worker_fds_lock:
            live = list(worker_fds)
        for fd in live:
            try:
                fd.flush()
                os.fsync(fd.fileno())
            except (OSError, ValueError):
                pass
        save_meta()

    # Calculate initial done bytes (includes resumed bytes).
    done = [sum(s.have for s in segments)]

    # Rate limiting: an explicitly passed TokenBucket wins; otherwise the
    # legacy speed_limit is wrapped in a TokenBucket for backward compatibility.
    bucket = rate_limiter
    if bucket is None and speed_limit:
        bucket = TokenBucket(speed_limit)

    # Sliding-window rate over bytes transferred *this session* only.
    session_rate = _SlidingRate(window=3.0)

    limit_lock = threading.Lock()

    def throttled_on_bytes(n):
        with limit_lock:
            done[0] += n
        if bucket is not None:
            bucket.consume(n)
        session_rate.add(n)
        if status_cb:
            status_cb(done[0], total, time.time() - t0)
        if rate_cb is not None:
            rate_cb(session_rate.rate())

    def emit_parts():
        if parts_cb:
            with seg_lock:
                parts_cb([
                    {"start": s.start, "end": s.end, "have": s.have, "cursor": s.cursor}
                    for s in segments
                ])

    emit_parts()
    save_meta()

    errors = []
    err_lock = threading.Lock()

    # Shared abort: the first fatal worker error sets it so siblings stop.
    abort_event = threading.Event()

    # A 200 on segment 0 is a full stream only for a single-segment plan.
    single_segment = len(segments) == 1

    def run_worker(initial_seg):
        try:
            _worker_loop(
                url=url,
                seg=initial_seg,
                dest_path=part,
                segments=segments,
                seg_lock=seg_lock,
                stop_event=stop_event,
                on_bytes=throttled_on_bytes,
                extra_headers=headers,
                etag=etag,
                last_modified=last_modified,
                meta_saver=checkpoint,
                proxy=proxy,
                single_segment=single_segment,
                total=total,
                abort_event=abort_event,
                fd_registry=(worker_fds, worker_fds_lock),
                pending=pending,
            )
        except Paused:
            pass
        except _Aborted:
            pass
        except Exception as ex:
            with err_lock:
                if not errors:
                    errors.append(ex)
            abort_event.set()

    # Single checkpoint timer: persists the sidecar every ~2s (fsync first).
    ckpt_stop = threading.Event()

    def _checkpoint_loop():
        while not ckpt_stop.wait(2.0):
            try:
                checkpoint()
            except Exception:
                pass

    ckpt_thread = threading.Thread(target=_checkpoint_loop, daemon=True)
    ckpt_thread.start()

    # At most `conns` workers; remaining incomplete segments wait in a pending
    # queue that workers pick up before they try work stealing.
    pending: collections.deque[Segment] = collections.deque()
    incomplete = [s for s in segments if s.cursor <= s.end]
    initial_segments = incomplete[:conns]
    for s in incomplete[conns:]:
        pending.append(s)

    try:
        # Launch worker threads
        ths = []
        for seg in initial_segments:
            t = threading.Thread(target=run_worker, args=(seg,), daemon=True)
            ths.append(t)
            t.start()

        for t in ths:
            t.join()
    finally:
        ckpt_stop.set()
        ckpt_thread.join(timeout=10)

    if stop_event is not None and stop_event.is_set():
        checkpoint()
        emit_parts()
        raise Paused()

    checkpoint()
    emit_parts()

    if errors:
        raise errors[0]

    # Verification: check all segments completed
    with seg_lock:
        all_completed = all(s.cursor > s.end for s in segments)
    if not all_completed and total > 0:
        raise RuntimeError("Download completed without finishing all bytes.")

    if state_cb:
        state_cb("verifying")

    actual_size = total if total > 0 else os.path.getsize(part)
    sha = compute_sha256(part, actual_size)

    # Completion gate passed: atomically publish the .part as the final name.
    # The final name never exists before this point.
    os.replace(part, name)

    # Mark-of-the-Web (Windows only): Zone.Identifier ADS for the download.
    if sys.platform == "win32":
        referer = None
        if headers:
            for k, v in headers.items():
                if k.lower() == "referer":
                    referer = v
                    break
        write_zone_identifier(name, url, referer=referer)

    # Clean up sidecar file on verified completion
    sidecar_path = get_sidecar_path(name)
    if os.path.exists(sidecar_path):
        try:
            os.remove(sidecar_path)
        except Exception:
            pass

    dur = time.time() - t0
    return {
        "file": name,
        "size": actual_size,
        "seconds": round(dur, 2),
        "sha256": sha,
        "connections": len(segments),
    }
