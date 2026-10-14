"""Unit tests for OpenDM Self-Updating Mechanism (opendm/updater.py).

Covers:
- test_version_comparison: semantic version comparisons & pre-release handling
- test_github_release_parsing_and_sha256_extraction: parsing API JSON and SHA256SUMS.txt
- test_updater_enqueues_into_opendm_engine: verify update enqueuing with target path and SHA-256
- test_windows_detached_installer_launch: verify detached subprocess flags and clean exit handoff
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from opendm import engine, updater
from opendm.queue import QueueManager


def test_version_comparison():
    """Verify semantic version parsing and comparison."""
    assert updater.parse_version("v2.1.0") == (2, 1, 0)
    assert updater.parse_version("2.1.0") == (2, 1, 0)
    assert updater.parse_version("v2.0.10") == (2, 0, 10)
    assert updater.parse_version("v2.0.2") == (2, 0, 2)

    # Comparison assertions
    assert updater.parse_version("v2.1.0") > updater.parse_version("v2.0.0")
    assert updater.parse_version("v2.0.0") == updater.parse_version("2.0.0")
    assert updater.parse_version("v2.0.10") > updater.parse_version("v2.0.2")
    assert updater.parse_version("v3.0.0") > updater.parse_version("v2.9.9")

    # is_newer_version helper
    assert updater.is_newer_version("v2.1.0", "2.0.0") is True
    assert updater.is_newer_version("v2.0.0", "2.0.0") is False
    assert updater.is_newer_version("v1.9.9", "2.0.0") is False


def test_github_release_parsing_and_sha256_extraction():
    """Mock GitHub API and SHA256SUMS.txt responses to test release parsing."""
    mock_release_payload = {
        "tag_name": "v2.1.0",
        "body": "## What's New\n- Faster downloads\n- Bug fixes",
        "html_url": "https://github.com/shadowcerdic/OpenDM/releases/tag/v2.1.0",
        "assets": [
            {
                "name": "OpenDM-Setup.exe",
                "browser_download_url": "https://github.com/shadowcerdic/OpenDM/releases/download/v2.1.0/OpenDM-Setup.exe",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://github.com/shadowcerdic/OpenDM/releases/download/v2.1.0/SHA256SUMS.txt",
            },
            {
                "name": "OpenDM-Chrome-Extension.zip",
                "browser_download_url": "https://github.com/shadowcerdic/OpenDM/releases/download/v2.1.0/OpenDM-Chrome-Extension.zip",
            },
        ],
    }

    mock_sha256sums_text = """
# SHA256 checksums
a1b2c3d4e5f67890123456789012345678901234567890123456789012345678  OpenDM-Setup.exe
99887766554433221100aabbccddeeff99887766554433221100aabbccddeeff  OpenDM-Chrome-Extension.zip
"""

    # Test standalone sha256 sums parsing
    parsed_hash = updater.parse_sha256sums_text(mock_sha256sums_text, "OpenDM-Setup.exe")
    assert parsed_hash == "a1b2c3d4e5f67890123456789012345678901234567890123456789012345678"

    # Test full check_for_updates flow with mocked network
    class MockResponse:
        def __init__(self, data: bytes):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=15):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "releases/latest" in url:
            return MockResponse(json.dumps(mock_release_payload).encode("utf-8"))
        elif "SHA256SUMS.txt" in url:
            return MockResponse(mock_sha256sums_text.encode("utf-8"))
        raise ValueError(f"Unexpected url: {url}")

    with patch("urllib.request.OpenerDirector.open", side_effect=mock_urlopen):
        info = updater.check_for_updates()
        assert info is not None
        assert info["tag_name"] == "v2.1.0"
        assert info["is_newer"] is True
        assert info["installer_url"] == "https://github.com/shadowcerdic/OpenDM/releases/download/v2.1.0/OpenDM-Setup.exe"
        assert info["expected_sha256"] == "a1b2c3d4e5f67890123456789012345678901234567890123456789012345678"


def test_updater_enqueues_into_opendm_engine(tmp_path):
    """Verify update enqueuing with correct temp path."""
    target_path = updater.get_update_target_path("v2.1.0")
    assert target_path.endswith("OpenDM-Setup-v2_1_0.exe") or target_path.endswith("OpenDM-Setup-v2.1.0.exe")

    # Isolated state file: the default ~/.config/opendm/queue.json must not be
    # used here (item 11 reserves names of jobs loaded from queue.json, so
    # sharing the real state file across runs would change job.dest).
    # Deterministic destination: drop reservation artifacts (.part) left in the
    # system temp dir by previous runs; item 11 checks .part names on disk.
    for stale in (target_path, target_path + ".opendm.part"):
        try:
            os.remove(stale)
        except OSError:
            pass
    qm = QueueManager(str(tmp_path), state_file=str(tmp_path / "queue.json"))
    job = qm.add(
        url="https://example.com/OpenDM-Setup.exe",
        conns=4,
        dest_dir=os.path.dirname(target_path),
        suggested_filename=os.path.basename(target_path),
    )

    assert job.filename == os.path.basename(target_path)
    assert job.dest == target_path
    assert job.conns == 4


def test_verify_installer_match(tmp_path):
    """verify_installer returns True when the hash matches."""
    data = b"MZ_DUMMY_EXE" * 1000
    p = tmp_path / "OpenDM-Setup-v2_1_0.exe"
    p.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert updater.verify_installer(str(p), expected) is True


def test_verify_installer_mismatch(tmp_path):
    """verify_installer returns False (not raise) on hash mismatch."""
    data = b"MZ_DUMMY_EXE" * 1000
    p = tmp_path / "OpenDM-Setup-v2_1_0.exe"
    p.write_bytes(data)
    wrong = "0" * 64
    assert updater.verify_installer(str(p), wrong) is False


def test_verify_installer_missing_file(tmp_path):
    """verify_installer returns False (not raise) when the file is absent."""
    missing = str(tmp_path / "does-not-exist.exe")
    assert updater.verify_installer(missing, "ab" * 32) is False


def test_verify_installer_no_expected_hash(tmp_path):
    """verify_installer returns False when no expected hash is available."""
    p = tmp_path / "OpenDM-Setup-v2_1_0.exe"
    p.write_bytes(b"MZ_DUMMY_EXE")
    assert updater.verify_installer(str(p), None) is False
    assert updater.verify_installer(str(p), "") is False


def test_check_for_updates_uses_safe_redirect_handler(monkeypatch):
    """check_for_updates' opener must include engine.SafeRedirectHandler.

    The update check hits api.github.com and follows GitHub's redirects; the
    handler strips Cookie/Authorization on cross-origin redirects.
    """
    captured = {}
    real_build = urllib.request.build_opener

    def spy_build(*handlers):
        captured["handlers"] = handlers
        return real_build(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", spy_build)

    payload = {
        "tag_name": "v9.9.9",
        "body": "",
        "html_url": "https://github.com/shadowcerdic/OpenDM/releases/tag/v9.9.9",
        "assets": [],
    }

    class MockResponse:
        def __init__(self, data: bytes):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open",
        lambda self, req, timeout=15: MockResponse(
            json.dumps(payload).encode("utf-8")))

    info = updater.check_for_updates()
    assert info is not None and "error" not in info
    handlers = captured.get("handlers", ())
    assert any(isinstance(h, engine.SafeRedirectHandler) for h in handlers), \
        f"SafeRedirectHandler missing from opener handlers: {handlers!r}"


def test_fetch_expected_sha256_uses_safe_redirect_handler(monkeypatch):
    """fetch_expected_sha256() must use SafeRedirectHandler even without a
    caller-supplied opener (it falls back to building its own)."""
    captured = {}
    real_build = urllib.request.build_opener

    def spy_build(*handlers):
        captured["handlers"] = handlers
        return real_build(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", spy_build)

    sums = "a" * 64 + "  OpenDM-Setup.exe\n"

    class MockResponse:
        def __init__(self, data: bytes):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open",
        lambda self, req, timeout=15: MockResponse(sums.encode("utf-8")))

    result = updater.fetch_expected_sha256(
        "https://example.com/SHA256SUMS.txt", "OpenDM-Setup.exe")
    assert result == "a" * 64
    handlers = captured.get("handlers", ())
    assert any(isinstance(h, engine.SafeRedirectHandler) for h in handlers), \
        f"SafeRedirectHandler missing from opener handlers: {handlers!r}"


def test_windows_detached_installer_launch(tmp_path):
    """Verify detached subprocess invocation and clean exit handoff."""
    installer_file = tmp_path / "OpenDM-Setup-v2_1_0.exe"
    installer_file.write_bytes(b"MZ_DUMMY_EXE")

    clean_exit_called = []

    def mock_on_exit():
        clean_exit_called.append(True)

    with patch("subprocess.Popen") as mock_popen:
        updater.execute_installer_and_exit(str(installer_file), on_before_exit=mock_on_exit)

        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd[0] == str(installer_file)
        assert "/SILENT" in cmd
        assert "/CLOSEAPPLICATIONS" in cmd
        assert kwargs.get("close_fds") is True
        assert clean_exit_called == [True]
