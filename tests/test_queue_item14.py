"""Item 14: secrets at rest + config_dir(app=...) helper + shipped origins default.

- Cookie/Authorization are cleared from a job's headers when it completes
  or is cancelled; to_dict() excludes them for finished jobs
  (completed/canceled/error) so queue.json keeps no secrets.
- security.config_dir(app="opendm") is the single config-dir helper;
  queue.py uses it instead of duplicating the XDG logic. On Windows
  (sys.platform == "win32") it returns %APPDATA%\\OpenDM and an existing
  ~/.config/opendm/queue.json is migrated there.
  Windows behavior is tested with mocks on Linux; unverified on real Windows.
- security.load_or_create_config's shipped allowed_origins default is
  exactly ["chrome-extension://hnhmponoadleelhnecpijhnlpefkjflc"]
  (no wildcards).

Failing-first tests for the pre-fix tree.
"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from opendm import engine, security
from opendm.queue import Job, QueueManager

SECRET_HEADERS = {
    "Cookie": "session=abc123",
    "Authorization": "Bearer sekrit",
    "Referer": "https://example.com/",
    "User-Agent": "OpenDM/2.0",
}
REAL_ID_ORIGIN = "chrome-extension://hnhmponoadleelhnecpijhnlpefkjflc"


def _make_qm(tmp_path, **kw):
    return QueueManager(str(tmp_path / "downloads"),
                        state_file=str(tmp_path / "q.json"), **kw)


def _stub_download_ok(monkeypatch):
    def fake(url, **kw):
        return {"sha256": "x" * 64, "size": 1234}
    monkeypatch.setattr(engine, "download", fake)


# --- secrets at rest -------------------------------------------------------

def test_completed_job_clears_secret_headers(monkeypatch, tmp_path):
    qm = _make_qm(tmp_path)
    _stub_download_ok(monkeypatch)
    job = qm.add("http://127.0.0.1/f.bin", headers=dict(SECRET_HEADERS))
    assert "cookie" in {k.lower() for k in job.headers}
    job._run()  # synchronous completion
    assert job.state == "completed"
    lowered = {k.lower() for k in job.headers}
    assert "cookie" not in lowered, f"Cookie survived completion: {job.headers}"
    assert "authorization" not in lowered, f"Authorization survived: {job.headers}"
    assert "referer" in lowered  # non-secret headers are kept


def test_canceled_job_clears_secret_headers(tmp_path):
    qm = _make_qm(tmp_path)
    job = qm.add("http://127.0.0.1/f.bin", headers=dict(SECRET_HEADERS))
    job.cancel()  # no live worker -> immediate terminal "canceled"
    assert job.state == "canceled"
    lowered = {k.lower() for k in job.headers}
    assert "cookie" not in lowered, f"Cookie survived cancel: {job.headers}"
    assert "authorization" not in lowered, f"Authorization survived: {job.headers}"


@pytest.mark.parametrize("state", ["completed", "canceled", "error"])
def test_to_dict_excludes_secrets_for_finished_states(tmp_path, state):
    job = Job("http://127.0.0.1/f.bin", str(tmp_path),
              headers=dict(SECRET_HEADERS), state=state, filename="f.bin")
    headers = job.to_dict()["headers"]
    lowered = {k.lower() for k in headers}
    assert "cookie" not in lowered, f"Cookie in queue.json payload: {headers}"
    assert "authorization" not in lowered, f"Authorization in payload: {headers}"


@pytest.mark.parametrize("state", ["queued", "paused"])
def test_to_dict_keeps_headers_for_active_states(tmp_path, state):
    # Active/interrupted jobs still need their headers to resume.
    job = Job("http://127.0.0.1/f.bin", str(tmp_path),
              headers=dict(SECRET_HEADERS), state=state, filename="f.bin")
    lowered = {k.lower() for k in job.to_dict()["headers"]}
    assert "cookie" in lowered
    assert "authorization" in lowered


# --- config_dir(app=...) helper --------------------------------------------

def test_config_dir_default_and_app_param(monkeypatch, tmp_path):
    # POSIX branch: pin the platform so XDG_CONFIG_HOME is honored even on
    # real Windows (where config_dir() takes the %APPDATA% branch instead).
    with mock.patch.object(sys, "platform", "linux"):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert security.config_dir() == tmp_path / "opendm"
        assert security.config_dir(app="dl") == tmp_path / "dl"


def test_config_dir_windows_returns_appdata_opendm(monkeypatch, tmp_path):
    # Mocked on Linux; unverified on real Windows.
    appdata = tmp_path / "AppData" / "Roaming"
    with mock.patch.object(sys, "platform", "win32"):
        monkeypatch.setenv("APPDATA", str(appdata))
        assert security.config_dir() == appdata / "OpenDM"


def test_queue_state_file_uses_config_dir_helper(monkeypatch, tmp_path):
    with mock.patch.object(sys, "platform", "linux"):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        qm = QueueManager(str(tmp_path / "downloads"), state_file=None)
        assert qm.state_file == tmp_path / "opendm" / "queue.json"


def test_windows_migrates_legacy_queue_json(monkeypatch, tmp_path):
    # Mocked on Linux; unverified on real Windows.
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    legacy = home / ".config" / "opendm" / "queue.json"
    legacy.parent.mkdir(parents=True)
    payload = {"version": 1, "jobs": [{
        "id": 7, "url": "http://127.0.0.1/old.bin",
        "dest_dir": str(tmp_path / "downloads"),
        "filename": "old.bin", "dest": str(tmp_path / "downloads" / "old.bin"),
        "conns": 4, "state": "completed",
        "headers": {"Cookie": "stale=1"},
    }]}
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    # Windows' expanduser() reads USERPROFILE, not HOME.
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    with mock.patch.object(sys, "platform", "win32"):
        qm = QueueManager(str(tmp_path / "downloads"), state_file=None)

    target = appdata / "OpenDM" / "queue.json"
    assert qm.state_file == target, f"state_file={qm.state_file}"
    assert target.exists(), "migrated queue.json missing"
    assert not legacy.exists(), "legacy queue.json was not moved away"
    assert len(qm.jobs) == 1 and qm.jobs[0].id == 7
    # The stale secret must not be re-persisted for the finished job.
    lowered = {k.lower() for k in qm.jobs[0].to_dict()["headers"]}
    assert "cookie" not in lowered
