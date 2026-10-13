"""OpenDM multi-connection download engine with resume, range planning, and assembly."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from .sanitize import parse_content_disposition, sanitize_filename

UA = "OpenDM/1.0"
PART_DIR = ".opendm"


class Paused(Exception):
    """Raised inside workers when a stop was requested; parts stay on disk."""


def probe(url: str, extra_headers: dict | None = None) -> dict:
    """Probe server with HEAD request (falls back to Range: bytes=0-1)."""
    hdrs = {"User-Agent": UA}
    if extra_headers:
        for k, v in extra_headers.items():
            if k.lower() in ("cookie", "referer", "user-agent", "authorization"):
                hdrs[k] = v
    req = urllib.request.Request(url, method="HEAD", headers=hdrs)
    size = 0
    arange = ""
    cd_name = None
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            size = int(r.headers.get("Content-Length") or 0)
            arange = (r.headers.get("Accept-Ranges") or "").lower()
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
            with urllib.request.urlopen(req, timeout=20) as r:
                supports = r.status == 206 and r.headers.get("Content-Range") is not None
                if not size:
                    cr = r.headers.get("Content-Range") or ""
                    m = re.search(r"/(\d+)", cr)
                    if m:
                        size = int(m.group(1))
                if not cd_name and r.headers.get("Content-Disposition"):
                    cd_name = parse_content_disposition(r.headers.get("Content-Disposition"))
        except Exception:
            supports = False

    return {
        "size": size,
        "ranges": bool(supports),
        "content_disposition_filename": cd_name,
    }


def plan_ranges(total: int, n: int) -> list[tuple[int, int]]:
    """Contiguous split of [0, total-1] into up to n page-aligned segments."""
    if n <= 1 or total <= 0:
        return [(0, max(total - 1, 0))]
    seg = (total + n - 1) // n
    seg = ((seg + 4095) // 4096) * 4096
    out, s = [], 0
    while s < total:
        e = min(s + seg - 1, total - 1)
        out.append((s, e))
        s = e + 1
    return out


def _fetch_chunk(url: str, start: int, end: int, part_path: str,
                 stop_event=None, on_bytes=None, extra_headers: dict | None = None,
                 max_retries: int = 5) -> int:
    """Fetch [start, end] into part_path; resumes from current part size with auto-retry."""
    attempt = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            raise Paused()

        have = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        if have > (end - start + 1):
            with open(part_path, "r+b") as f:
                f.truncate(end - start + 1)
            have = end - start + 1
        pos = start + have
        if pos > end:
            return have

        hdrs = {"User-Agent": UA, "Range": f"bytes={pos}-{end}"}
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() in ("cookie", "referer", "user-agent", "authorization"):
                    hdrs[k] = v

        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status not in (200, 206):
                    raise RuntimeError(f"unexpected status {r.status}")
                if r.status == 200 and have:
                    os.remove(part_path)
                    attempt += 1
                    continue
                if r.status == 200 and not have:
                    remaining = end - start + 1
                    with open(part_path, "wb") as f:
                        while remaining > 0:
                            if stop_event is not None and stop_event.is_set():
                                raise Paused()
                            buf = r.read(min(65536, remaining))
                            if not buf:
                                break
                            f.write(buf)
                            have += len(buf)
                            remaining -= len(buf)
                            if on_bytes:
                                on_bytes(len(buf))
                    return have
                with open(part_path, "ab") as f:
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            raise Paused()
                        buf = r.read(65536)
                        if not buf:
                            break
                        f.write(buf)
                        have += len(buf)
                        if on_bytes:
                            on_bytes(len(buf))
            return have
        except Paused:
            raise
        except Exception as ex:
            attempt += 1
            if attempt > max_retries or (stop_event is not None and stop_event.is_set()):
                raise RuntimeError(f"Network error after {attempt} attempts: {ex}")
            # Exponential backoff with small sleep
            backoff = min(2 ** attempt * 0.5, 5.0)
            t_end = time.time() + backoff
            while time.time() < t_end:
                if stop_event is not None and stop_event.is_set():
                    raise Paused()
                time.sleep(0.2)


def assemble(dest: str, total: int, parts: list[str]) -> str:
    tmp = dest + ".asm"
    h = hashlib.sha256()
    with open(tmp, "wb") as out:
        for p in sorted(parts):
            with open(p, "rb") as f:
                while True:
                    b = f.read(262144)
                    if not b:
                        break
                    h.update(b)
                    out.write(b)
    if total > 0 and os.path.getsize(tmp) != total:
        os.remove(tmp)
        raise RuntimeError(f"assembled {os.path.getsize(tmp)} != expected {total}")
    os.replace(tmp, dest)
    for p in parts:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    return h.hexdigest()


def download(url: str, dest: str | None = None, conns: int = 4,
             status_cb=None, state_cb=None, parts_cb=None, stop_event=None,
             speed_limit: int = 0, headers: dict | None = None) -> dict:
    t0 = time.time()
    meta = probe(url, extra_headers=headers)
    total = meta["size"]

    if not dest:
        cand = meta.get("content_disposition_filename")
        if not cand:
            path_part = urllib.parse.urlparse(url).path
            cand = os.path.basename(re.sub(r"[?#].*$", "", path_part)) or "download.bin"
        name = sanitize_filename(cand)
    else:
        name = dest

    workdir = os.path.join(os.path.dirname(os.path.abspath(name)), PART_DIR)
    os.makedirs(workdir, exist_ok=True)
    state_path = os.path.join(workdir, os.path.basename(name) + ".json")

    done = [0]
    limit_lock = threading.Lock()
    limit_t0 = [time.time()]
    limit_bytes = [0]

    def throttled_on_bytes(n):
        with limit_lock:
            done[0] += n
            limit_bytes[0] += n
        if speed_limit:
            while limit_bytes[0] / max(time.time() - limit_t0[0], 1e-3) > speed_limit:
                time.sleep(0.01)
        if status_cb:
            status_cb(done[0], total, time.time() - t0)

    ranges = plan_ranges(total, conns) if (meta["ranges"] and total > 0) else [(0, max(total - 1, 0))]
    json.dump({"url": url, "total": total, "ranges": ranges}, open(state_path, "w"))
    parts = [os.path.join(workdir, f"{os.path.basename(name)}.{i:02d}.part")
             for i in range(len(ranges))]
    for i, (s, e) in enumerate(ranges):
        done[0] += os.path.getsize(parts[i]) if os.path.exists(parts[i]) else 0

    def emit_parts():
        if parts_cb:
            parts_cb([{"start": s, "end": e,
                       "have": os.path.getsize(p) if os.path.exists(p) else 0}
                      for (s, e), p in zip(ranges, parts)])

    emit_parts()

    errors = []

    def worker(i, s, e):
        try:
            _fetch_chunk(url, s, e, parts[i], stop_event=stop_event,
                         on_bytes=throttled_on_bytes, extra_headers=headers)
        except Paused:
            pass
        except Exception as ex:
            errors.append((i, ex))

    ths = [threading.Thread(target=worker, args=(i, s, e), daemon=True)
           for i, (s, e) in enumerate(ranges)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    if stop_event is not None and stop_event.is_set():
        emit_parts()
        raise Paused()

    if errors:
        raise RuntimeError(f"{len(errors)} connection(s) failed: {errors[0][1]!r} "
                           f"— parts kept on disk; rerun to resume")

    if state_cb:
        state_cb("assembling")
    actual_total = total if total > 0 else sum(os.path.getsize(p) for p in parts if os.path.exists(p))
    sha = assemble(name, actual_total, parts)
    try:
        os.remove(state_path)
    except FileNotFoundError:
        pass
    dur = time.time() - t0
    return {"file": name, "size": actual_total, "seconds": round(dur, 2),
            "sha256": sha, "connections": len(ranges)}
