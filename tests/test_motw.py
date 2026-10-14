"""Item 20: Mark-of-the-Web (Zone.Identifier ADS) tests.

Writes ``<file>:Zone.Identifier`` with [ZoneTransfer]/ZoneId=3 after the
final os.replace(part, name), Windows-only (sys.platform == "win32" guard),
best-effort (OSError swallowed).

NOTE -- UNVERIFIED ON REAL WINDOWS: all Windows behavior below is exercised
with sys.platform mocked to "win32" and a mocked ADS write. No test in this
file has ever run on a real Windows/NTFS machine.
"""
from __future__ import annotations

import builtins
import os
import sys
import types

import pytest

from conftest import make_server, PAYLOAD, PAYLOAD_SHA256
from opendm import engine


def _mock_win32(monkeypatch):
    """Pretend engine is running on Windows.

    Patches engine.sys (not the global sys module): mocking sys.platform
    globally makes CPython's ssl module take a Windows-only code path
    (enum_certificates) that does not exist on Linux, which breaks
    urllib's HTTPSHandler construction during the test downloads. Patching
    the name engine looks up keeps the mock scoped to the code under test.
    """
    monkeypatch.setattr(engine, "sys", types.SimpleNamespace(platform="win32"))


class _AdsFile:
    """Minimal writable context manager capturing one ADS write."""

    def __init__(self, store, path):
        self._store = store
        self._path = path
        self._chunks = []

    def write(self, s):
        self._chunks.append(s)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._store[self._path] = "".join(self._chunks)
        return False


def _patch_ads_open(monkeypatch, fail=False):
    """Replace builtins.open: capture :Zone.Identifier writes, delegate rest.

    Returns the dict mapping ADS path -> content written.
    """
    writes = {}
    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if str(file).endswith(":Zone.Identifier"):
            if fail:
                raise OSError(22, "mocked non-NTFS filesystem")
            return _AdsFile(writes, str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    return writes


# ---------------------------------------------------------------------------
# Content of the Zone.Identifier stream (platform mocked to win32)
# ---------------------------------------------------------------------------

def test_zone_identifier_content_with_referer(monkeypatch, tmp_path):
    """Stream has [ZoneTransfer], ZoneId=3, stripped HostUrl, ReferrerUrl."""
    _mock_win32(monkeypatch)
    writes = _patch_ads_open(monkeypatch)

    final = str(tmp_path / "dl.bin")
    engine.write_zone_identifier(
        final,
        "https://user:pass@example.com:8443/a/b?x=1#frag",
        referer="https://search.example/q",
    )

    ads_path = f"{final}:Zone.Identifier"
    assert ads_path in writes, "expected an ADS write"
    ads = writes[ads_path]
    assert "[ZoneTransfer]" in ads
    assert "ZoneId=3" in ads
    assert "ReferrerUrl=https://search.example/q" in ads
    assert "HostUrl=https://example.com:8443/a/b?x=1#frag" in ads
    assert "user:pass" not in ads, "userinfo must be stripped from HostUrl"


def test_zone_identifier_omits_referrer_line_when_no_referer(monkeypatch, tmp_path):
    """Without a referer, the ReferrerUrl line must be absent (not empty)."""
    _mock_win32(monkeypatch)
    writes = _patch_ads_open(monkeypatch)

    final = str(tmp_path / "dl.bin")
    engine.write_zone_identifier(final, "https://example.com/f.bin")

    ads = writes[f"{final}:Zone.Identifier"]
    assert "[ZoneTransfer]" in ads
    assert "ZoneId=3" in ads
    assert "ReferrerUrl" not in ads
    assert "HostUrl=https://example.com/f.bin" in ads


@pytest.mark.parametrize("hdr_key", ["Referer", "referer", "REFERER", "rEfErEr"])
def test_download_passes_referer_case_insensitive(monkeypatch, tmp_path, hdr_key):
    """download() extracts the referer header case-insensitively on win32."""
    _mock_win32(monkeypatch)
    calls = []

    def fake_write(final_path, url, referer=None):
        calls.append((final_path, url, referer))

    monkeypatch.setattr(engine, "write_zone_identifier", fake_write)

    with make_server() as server:
        url = server.url("/file.bin")
        dest = str(tmp_path / "dl.bin")
        res = engine.download(
            url, dest=dest, conns=2, headers={hdr_key: "https://ref.example/"}
        )

    assert res["file"] == dest
    assert len(calls) == 1, f"expected exactly one ADS write, got {len(calls)}"
    final_path, got_url, got_referer = calls[0]
    assert final_path == dest
    assert got_url == url
    assert got_referer == "https://ref.example/"


def test_download_passes_none_referer_when_no_header(monkeypatch, tmp_path):
    """No referer header -> write_zone_identifier called with referer=None."""
    _mock_win32(monkeypatch)
    calls = []
    monkeypatch.setattr(
        engine, "write_zone_identifier",
        lambda final_path, url, referer=None: calls.append((final_path, url, referer)),
    )

    with make_server() as server:
        dest = str(tmp_path / "dl.bin")
        res = engine.download(server.url("/file.bin"), dest=dest, conns=2)

    assert res["file"] == dest
    assert len(calls) == 1
    assert calls[0][2] is None


def test_download_end_to_end_writes_ads_stream(monkeypatch, tmp_path):
    """Full download on mocked win32 produces the correct ADS content."""
    _mock_win32(monkeypatch)
    writes = _patch_ads_open(monkeypatch)

    with make_server() as server:
        url = server.url("/file.bin")
        dest = str(tmp_path / "dl.bin")
        res = engine.download(
            url, dest=dest, conns=2,
            headers={"Referer": "https://search.example/q"},
        )

    assert res["file"] == dest
    assert os.path.exists(dest)
    ads = writes.get(f"{dest}:Zone.Identifier")
    assert ads is not None, "expected :Zone.Identifier to be written"
    assert "[ZoneTransfer]" in ads
    assert "ZoneId=3" in ads
    assert "ReferrerUrl=https://search.example/q" in ads
    assert f"HostUrl={url}" in ads


# ---------------------------------------------------------------------------
# No-op on non-Windows
# ---------------------------------------------------------------------------

def test_write_zone_identifier_noop_on_linux(monkeypatch, tmp_path):
    """On the real (non-win32) platform the function must not attempt a write."""
    if sys.platform == "win32":
        pytest.skip("Linux/macOS-only assertion")
    assert sys.platform != "win32"

    def boom(file, mode="r", *args, **kwargs):
        raise AssertionError("must not attempt any file write on non-Windows")

    monkeypatch.setattr(builtins, "open", boom)
    # Must return silently without touching the filesystem.
    engine.write_zone_identifier(
        str(tmp_path / "x.bin"), "https://example.com/f",
        referer="https://r.example/",
    )


def test_download_completes_on_linux_without_ads_attempt(monkeypatch, tmp_path):
    """download() on Linux completes and never calls write_zone_identifier."""
    if sys.platform == "win32":
        pytest.skip("Linux/macOS-only assertion")

    def boom(*args, **kwargs):
        raise AssertionError(
            "write_zone_identifier must not be attempted on non-Windows"
        )

    monkeypatch.setattr(engine, "write_zone_identifier", boom)

    with make_server() as server:
        dest = str(tmp_path / "dl.bin")
        res = engine.download(
            server.url("/file.bin"), dest=dest, conns=2,
            headers={"Referer": "https://ref.example/"},
        )

    assert res["file"] == dest
    assert res["size"] == len(PAYLOAD)
    assert os.path.exists(dest)
    with open(dest, "rb") as f:
        import hashlib
        assert hashlib.sha256(f.read()).hexdigest() == PAYLOAD_SHA256


# ---------------------------------------------------------------------------
# Best effort: OSError swallowed
# ---------------------------------------------------------------------------

def test_oserror_during_ads_write_is_swallowed(monkeypatch, tmp_path):
    """An OSError on the ADS write must not fail the function or download."""
    _mock_win32(monkeypatch)
    writes = _patch_ads_open(monkeypatch, fail=True)

    # Direct call: must not raise.
    engine.write_zone_identifier(
        str(tmp_path / "x.bin"), "https://example.com/f",
        referer="https://r.example/",
    )

    # Full download on mocked win32 still reports success.
    with make_server() as server:
        dest = str(tmp_path / "dl.bin")
        res = engine.download(server.url("/file.bin"), dest=dest, conns=2)

    assert res["file"] == dest
    assert res["size"] == len(PAYLOAD)
    assert writes == {}, "no ADS content should have been captured"
