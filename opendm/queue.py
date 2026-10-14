"""OpenDM download queue with persistent state (JSON) and sanitized paths."""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import engine
from .sanitize import sanitize_filename, unique_destination
from .security import config_dir

STATES = ("queued", "downloading", "paused", "completed", "error", "assembling", "canceled")

# How long resume() waits for a previous worker thread to exit before
# starting a fresh one.
_WORKER_JOIN_TIMEOUT = 10.0

DEFAULT_CATEGORIES = {
    "General": ("*",),
    "Compressed": ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"),
    "Video": ("mp4", "mkv", "avi", "mov", "flv", "webm", "ts", "m4v"),
    "Music": ("mp3", "flac", "wav", "ogg", "m4a", "opus", "aac"),
    "Documents": ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub"),
    "Programs": ("exe", "msi", "apk", "deb", "rpm", "dmg", "AppImage"),
}


def category_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for cat, exts in DEFAULT_CATEGORIES.items():
        if ext in exts:
            return cat
    return "General"


def _coerce_filename(url: str, suggested_filename: str | None) -> str:
    """Derive a sanitized bare filename.

    Precedence: sanitized suggested name (bare filename only)
    > URL basename > default. Sanitized server-side regardless.
    """
    filename = None
    if suggested_filename:
        filename = sanitize_filename(
            os.path.basename(suggested_filename.replace("\\", "/")) or "download.bin")
    if not filename:
        path_part = urllib.parse.urlparse(url).path
        raw = os.path.basename(path_part) or "download.bin"
        filename = sanitize_filename(raw)
    return filename


def _part_path(final_path: str) -> str:
    """Path of the in-progress download file for a final destination.

    Uses engine.part_path once the engine worker lands it; falls back to
    the contracted "<final>.opendm.part" suffix.
    """
    pp = getattr(engine, "part_path", None)
    if callable(pp):
        return str(pp(final_path))
    return str(final_path) + ".opendm.part"


class Job:
    _next_id = 1
    _id_lock = threading.Lock()

    def __init__(
        self,
        url: str,
        dest_dir: str,
        conns: int = 4,
        headers: dict | None = None,
        job_id: int | None = None,
        state: str = "queued",
        filename: str | None = None,
        suggested_filename: str | None = None,
        dest: str | None = None,
    ):
        with Job._id_lock:
            if job_id is not None:
                self.id = job_id
                Job._next_id = max(Job._next_id, job_id + 1)
            else:
                self.id = Job._next_id
                Job._next_id += 1

        self.url = url
        self.dest_dir = dest_dir
        self.conns = conns
        # Whitelist safe headers; never log or expose sensitive headers
        self.headers = {k: v for k, v in (headers or {}).items()
                        if k.lower() in ("cookie", "referer", "user-agent", "authorization")}

        self.filename = _coerce_filename(url, suggested_filename) if filename is None else filename

        if dest is not None:
            # Destination pre-reserved by QueueManager (atomic); use as-is.
            self.dest = str(dest)
            self.filename = os.path.basename(self.dest)
        else:
            # Unique destination path (never overwrites existing file).
            # NOTE: not atomic across threads; QueueManager.add() reserves
            # atomically and passes dest explicitly.
            self.dest = str(unique_destination(dest_dir, self.filename)) if state == "queued" else os.path.join(dest_dir, self.filename)
            self.filename = os.path.basename(self.dest)

        self.category = category_for(self.filename)
        self.state = state
        self.total = 0
        self.done = 0
        self.speed = 0.0
        self.speed_avg = 0.0
        self.parts: list[dict] = []
        self.error = None
        self.sha256 = None
        self.created = time.time()
        self.finished = None
        self._stop = threading.Event()
        self._thread = None
        self._on_change = None
        # Intent flag: set by cancel(), read by the worker thread in _run so
        # the terminal "canceled" state is never overwritten by a stale
        # Paused/exception handler. Cleared on every fresh start().
        self._cancel_requested = False
        # Back-reference to the owning QueueManager (set by add()/_load());
        # provides the manager's proxy and shared rate limiter to _run.
        self._manager: QueueManager | None = None

    def start(self):
        # Canceled jobs are terminal: start() is a no-op for them, just
        # like resume() (see cancel()).
        if self.state == "canceled":
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._cancel_requested = False
        self.state = "downloading"
        self._notify()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        self._stop.set()
        if self.state == "downloading":
            self.state = "paused"
            self._notify()

    def cancel(self):
        """Cancel the job permanently.

        Records the cancel intent, stops the worker, removes the .part
        file and sidecar, and moves the job to the terminal "canceled"
        state. If a worker thread is in flight it transitions to
        "canceled" itself when it exits (see _run); otherwise the state
        is set immediately. A canceled job cannot be resumed: resume()
        (and start()) are documented no-ops.
        """
        self._cancel_requested = True
        self._stop.set()
        self._remove_part_and_sidecar()
        self._clear_secret_headers()
        thr = self._thread
        if not (thr and thr.is_alive()):
            self.state = "canceled"
            self.error = "canceled"
            self.finished = time.time()
            self._notify()
        # else: the running worker sets state="canceled" in _run on exit.

    def _clear_secret_headers(self):
        """Drop Cookie/Authorization from the job's headers (secrets at rest).

        Rebinds self.headers to a new dict rather than mutating in place,
        so an in-flight engine.download call holding the old dict
        reference is unaffected.
        """
        self.headers = {k: v for k, v in self.headers.items()
                        if k.lower() not in ("cookie", "authorization")}

    def _remove_part_and_sidecar(self):
        """Best-effort removal of the in-progress .part file and sidecars."""
        part = _part_path(self.dest)
        candidates = {part}
        scp = getattr(engine, "get_sidecar_path", None)
        if callable(scp):
            try:
                candidates.add(scp(self.dest))
                candidates.add(scp(part))
            except Exception:
                pass
        candidates.add(self.dest + ".opendm")
        candidates.add(part + ".opendm")
        for p in candidates:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def resume(self):
        """Resume a paused or errored job.

        If the previous worker thread is still exiting (e.g. resume()
        called right after pause()), waits for it (join with timeout)
        before starting a fresh worker -- never starts a second worker on
        top of a live one, and never silently no-ops while the old thread
        is still running (raises RuntimeError if it refuses to exit).

        No-op on canceled jobs: cancellation is terminal (see cancel()).
        """
        if self.state == "canceled":
            return
        if self.state in ("paused", "error"):
            thr = self._thread
            if (thr is not None and thr is not threading.current_thread()
                    and thr.is_alive()):
                thr.join(timeout=_WORKER_JOIN_TIMEOUT)
                if thr.is_alive():
                    raise RuntimeError(
                        f"job {self.id}: previous worker thread did not exit")
            self.start()

    def _notify(self):
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass

    def _engine_download_kwargs(self) -> dict:
        """Resolve proxy/rate_limiter kwargs for engine.download.

        Uses the owning manager's proxy and shared token bucket. Defensive
        about the engine side (TokenBucket / download(rate_limiter=...)):
        when a speed limit was requested but the engine rate-limiter
        support has not landed, raises a clear RuntimeError instead of
        silently downloading unlimited.
        """
        mgr = getattr(self, "_manager", None)
        proxy = getattr(mgr, "proxy", None)
        limiter = getattr(mgr, "token_bucket", None)
        if limiter is None and getattr(mgr, "speed_limit", 0) > 0:
            raise RuntimeError(
                "speed limit requested but engine.TokenBucket is not "
                "available (engine rate-limiter support has not landed yet)")
        kwargs: dict = {"proxy": proxy}
        if limiter is not None:
            params = inspect.signature(engine.download).parameters
            if ("rate_limiter" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in params.values())):
                kwargs["rate_limiter"] = limiter
            else:
                raise RuntimeError(
                    "engine.download() does not accept rate_limiter "
                    "(engine rate-limiter support has not landed yet)")
        return kwargs

    def _run(self):
        kwargs = self._engine_download_kwargs()
        try:
            res = engine.download(
                self.url,
                dest=self.dest,
                conns=self.conns,
                status_cb=self._progress,
                stop_event=self._stop,
                parts_cb=self._parts,
                headers=self.headers,
                **kwargs,
            )
            self.sha256 = res.get("sha256")
            self.total = res.get("size", self.total)
            self.done = self.total
            if self._cancel_requested:
                # Cancel won the race with a successful return: the terminal
                # "canceled" state must not be overwritten by "completed".
                self.state = "canceled"
                self.error = "canceled"
            else:
                self.state = "completed"
            self._clear_secret_headers()
            self.finished = time.time()
            self._notify()
        except engine.Paused:
            if self._cancel_requested:
                self.state = "canceled"
                self.error = "canceled"
                self._clear_secret_headers()
                self.finished = time.time()
            else:
                self.state = "paused"
            self._notify()
        except Exception as ex:
            if self._cancel_requested:
                # e.g. cancel() deleted the .part file out from under the
                # worker: the cancel intent still wins over "error".
                self.state = "canceled"
                self.error = "canceled"
                self._clear_secret_headers()
                self.finished = time.time()
                self._notify()
            elif self.state != "error":
                self.state = "error"
                self.error = str(ex)
                self._notify()

    def _progress(self, done, total, dt):
        self.done, self.total = done, total
        self.speed = done / dt if dt else 0
        self.speed_avg = done / dt if dt else 0

    def _parts(self, parts):
        self.parts = parts

    def to_dict(self) -> dict:
        # Finished jobs keep no secrets in queue.json: Cookie/Authorization
        # are excluded for completed/canceled/error states. Active or
        # interrupted (paused) jobs keep their headers so they can resume.
        headers = self.headers
        if self.state in ("completed", "canceled", "error"):
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("cookie", "authorization")}
        return {
            "id": self.id,
            "url": self.url,
            "dest_dir": self.dest_dir,
            "filename": self.filename,
            "dest": self.dest,
            "conns": self.conns,
            "state": "paused" if self.state == "downloading" else self.state,
            "category": self.category,
            "total": self.total,
            "done": self.done,
            "sha256": self.sha256,
            "error": self.error,
            "created": self.created,
            "finished": self.finished,
            "headers": headers,
        }

    def as_row(self) -> dict:
        return {
            "id": self.id, "url": self.url, "filename": self.filename,
            "state": self.state, "category": self.category,
            "total": self.total, "done": self.done,
            "percent": round(self.done * 100 / self.total, 1) if self.total else 0.0,
            "speed": self.speed_avg, "conns": self.conns,
            "sha256": self.sha256, "error": self.error,
        }


class QueueManager:
    # Class-level lock making destination choice atomic across threads and
    # across QueueManager instances sharing a directory.
    _dest_lock = threading.Lock()

    def __init__(self, dest_dir: str, state_file: str | Path | None = None,
                 *, proxy: str | None = None, speed_limit: int = 0):
        self.dest_dir = dest_dir
        os.makedirs(dest_dir, exist_ok=True)
        self.jobs: list[Job] = []
        self.lock = threading.Lock()
        # In-process reservation set (absolute final paths) so two queued jobs
        # never pick the same destination even when nothing exists on disk yet.
        self._reserved: set[str] = set()

        # Network settings shared by all jobs: an optional proxy URL and a
        # global download speed limit (bytes/sec, 0 = unlimited) enforced by
        # one shared token bucket. The bucket is created defensively: the
        # engine side (engine.TokenBucket) may not have landed yet, in which
        # case token_bucket stays None and _run raises a clear error only if
        # a nonzero limit was actually requested.
        self.proxy = proxy
        self.speed_limit = max(0, int(speed_limit or 0))
        _TokenBucket = getattr(engine, "TokenBucket", None)
        self.token_bucket = (_TokenBucket(self.speed_limit)
                             if _TokenBucket is not None else None)

        if state_file is None:
            sf = config_dir() / "queue.json"
            if sys.platform == "win32":
                # One-time migration: a queue.json written by the old
                # POSIX-style location (~/.config/opendm/) moves to
                # %APPDATA%\OpenDM on first run. Guarded by sys.platform;
                # tested with mocks on Linux, unverified on real Windows.
                legacy = Path(os.path.expanduser("~/.config")) / "opendm" / "queue.json"
                if legacy.exists() and not sf.exists():
                    try:
                        sf.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(os.fspath(legacy), os.fspath(sf))
                    except OSError:
                        pass
        else:
            sf = Path(state_file)
        self.state_file = sf
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("jobs", []):
                j = Job(
                    url=d["url"],
                    dest_dir=d.get("dest_dir", self.dest_dir),
                    conns=d.get("conns", 4),
                    headers=d.get("headers"),
                    job_id=d.get("id"),
                    state="paused" if d.get("state") in ("downloading", "queued") else d.get("state", "paused"),
                    filename=d.get("filename"),
                    dest=d.get("dest"),
                )
                j.total = d.get("total", 0)
                j.done = d.get("done", 0)
                j.sha256 = d.get("sha256")
                j.error = d.get("error")
                j.dest = d.get("dest", j.dest)
                j._on_change = self._save
                j._manager = self
                self.jobs.append(j)
                # Reserve names of jobs loaded from queue.json so new adds
                # cannot collide with them.
                with QueueManager._dest_lock:
                    self._reserved.add(os.path.abspath(j.dest))
        except Exception:
            pass

    def _reserve_destination(self, dest_dir: str, filename: str) -> str:
        """Atomically pick a unique destination and reserve it.

        Under the class-level lock, rejects a candidate when the final path
        exists on disk, its .part file exists on disk, or it is already
        reserved in-process; then exclusively creates the .part file
        (O_CREAT|O_EXCL guards against races with other processes) and
        records the reservation. Retries with a " (n)" suffix on collision.
        """
        d = Path(dest_dir)
        d.mkdir(parents=True, exist_ok=True)
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        with QueueManager._dest_lock:
            i = 0
            while True:
                cand = d / filename if i == 0 else d / f"{stem} ({i}){suffix}"
                part = _part_path(str(cand))
                key = os.path.abspath(cand)
                taken = cand.exists() or os.path.exists(part) or key in self._reserved
                if not taken:
                    try:
                        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                    except FileExistsError:
                        i += 1  # lost a cross-process race; try the next name
                        continue
                    os.close(fd)
                    self._reserved.add(key)
                    return str(cand)
                i += 1

    def _save(self):
        with self.lock:
            data = {"version": 1, "jobs": [j.to_dict() for j in self.jobs]}
            tmp = self.state_file.with_suffix(".tmp")
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                fd = os.open(tmp, flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, self.state_file)
            except Exception:
                pass

    def add(self, url: str, conns: int = 4, dest_dir: str | None = None,
            headers: dict | None = None, suggested_filename: str | None = None) -> Job:
        dd = dest_dir or self.dest_dir
        filename = _coerce_filename(url, suggested_filename)
        dest = self._reserve_destination(dd, filename)
        job = Job(url, dd, conns=conns, headers=headers,
                  filename=filename, dest=dest)
        job._on_change = self._save
        job._manager = self
        with self.lock:
            self.jobs.append(job)
        self._save()
        return job

    def set_speed_limit(self, bytes_per_sec: int):
        """Change the shared download speed limit live (0 = unlimited).

        Applies immediately to the shared token bucket used by every job,
        via token_bucket.set_limit().
        """
        self.speed_limit = max(0, int(bytes_per_sec or 0))
        if self.token_bucket is not None:
            self.token_bucket.set_limit(self.speed_limit)

    def start_all(self):
        for j in self.jobs:
            if j.state in ("queued", "paused"):
                j.start()

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [j.as_row() for j in self.jobs]

    def by_id(self, jid: int) -> Job | None:
        for j in self.jobs:
            if j.id == jid:
                return j
        return None
