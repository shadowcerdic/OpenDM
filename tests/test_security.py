"""Security unit tests for OpenDM daemon and config."""
import asyncio
import json
import os
import tempfile
import urllib.request
import urllib.error
import pytest

from opendm import security


def test_origin_allowed_wildcard_rejected():
    assert not security.origin_allowed("https://evil.example", ["*"])
    assert not security.origin_allowed("https://evil.example", ["chrome-extension://*"])
    assert security.origin_allowed("chrome-extension://abcdef12345", ["chrome-extension://*"])
    assert security.origin_allowed("moz-extension://abcdef", ["moz-extension://*"])
    assert not security.origin_allowed(None, ["chrome-extension://*"])


def test_host_rebinding_rejected():
    assert security.host_allowed("127.0.0.1:10010")
    assert security.host_allowed("localhost:10010")
    assert not security.host_allowed("evil.example:10010")
    assert not security.host_allowed("attacker.com")
    assert not security.host_allowed(None)


def test_shipped_default_origins_have_no_wildcards(tmp_path, monkeypatch):
    # Item 14: the shipped default in load_or_create_config is exactly the
    # real extension ID (verified from extension/manifest.json) -- no
    # chrome-extension://* or moz-extension://* wildcards.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = security.load_or_create_config()
    assert cfg["allowed_origins"] == [
        "chrome-extension://hnhmponoadleelhnecpijhnlpefkjflc"
    ]
    assert not any("*" in o for o in cfg["allowed_origins"])


def test_token_file_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = security.load_or_create_config()
    p = security.config_path()
    assert p.exists()
    assert len(cfg["token"]) == 64
    mode = oct(p.stat().st_mode & 0o777)
    assert mode == "0o600", f"expected 0600, got {mode}"
