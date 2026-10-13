"""OpenDM download queue with persistent state (JSON) and sanitized paths."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from pathlib import Path

from . import engine
from .sanitize import sanitize_filename, unique_destination

STATES = ("queued", "downloading", "paused", "completed", "error", "assembling")

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

        # Filename precedence: sanitized suggested name (bare filename only)
        # > URL basename > default. Sanitized server-side regardless.
        if suggested_filename:
            filename = sanitize_filename(
                os.path.basename(suggested_filename.replace("\\", "/")) or "download.bin")
        if not filename:
            path_part = urllib.parse.urlparse(url).path
            raw = os.path.basename(path_part) or "download.bin"
            filename = sanitize_filename(raw)
        self.filename = filename

        # Unique destination path (never overwrites existing file)
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

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
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
        self._stop.set()
        self.state = "error"
        self.error = "canceled"
        self._notify()

    def resume(self):
        if self.state in ("paused", "error"):
            self.start()

    def _notify(self):
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass

    def _run(self):
        try:
            res = engine.download(
                self.url,
                dest=self.dest,
                conns=self.conns,
                status_cb=self._progress,
                stop_event=self._stop,
                parts_cb=self._parts,
                headers=self.headers,
            )
            self.sha256 = res.get("sha256")
            self.total = res.get("size", self.total)
            self.done = self.total
            self.state = "completed"
            self.finished = time.time()
            self._notify()
        except engine.Paused:
            self.state = "paused"
            self._notify()
        except Exception as ex:
            if self.state != "error":
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
            "headers": self.headers,
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
    def __init__(self, dest_dir: str, state_file: str | Path | None = None):
        self.dest_dir = dest_dir
        os.makedirs(dest_dir, exist_ok=True)
        self.jobs: list[Job] = []
        self.lock = threading.Lock()

        if state_file is None:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
            sf = Path(base) / "opendm" / "queue.json"
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
                )
                j.total = d.get("total", 0)
                j.done = d.get("done", 0)
                j.sha256 = d.get("sha256")
                j.error = d.get("error")
                j.dest = d.get("dest", j.dest)
                j._on_change = self._save
                self.jobs.append(j)
        except Exception:
            pass

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
        job = Job(url, dest_dir or self.dest_dir, conns=conns, headers=headers,
                  suggested_filename=suggested_filename)
        job._on_change = self._save
        with self.lock:
            self.jobs.append(job)
        self._save()
        return job

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
