"""OpenDM Self-Updating Mechanism via GitHub Releases API and OpenDM Engine.

Handles:
- Semantic version parsing and comparison
- Querying GitHub Releases API (with optional HTTP/SOCKS5 proxy)
- Automated SHA-256 lookup from SHA256SUMS.txt
- Dogfooding download via OpenDM's multi-connection engine
- Detached execution of Windows installer and clean application hand-off
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from typing import Callable

from . import __version__, engine

GITHUB_API_LATEST = "https://api.github.com/repos/shadowcerdic/OpenDM/releases/latest"
INSTALLER_ASSET_NAME = "OpenDM-Setup.exe"
SHA256_ASSET_NAME = "SHA256SUMS.txt"


def parse_version(tag: str) -> tuple[int, ...]:
    """Parse semantic version tag into integer tuple (e.g., 'v2.1.0' -> (2, 1, 0)).

    Strips leading 'v' or 'V' and ignores build/prerelease suffixes for primary comparison.
    """
    clean = re.sub(r"^[vV]", "", tag.strip())
    # Extract only digit groups separated by dots
    base = re.split(r"[-+]", clean)[0]
    parts = []
    for p in base.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(remote_tag: str, current_version: str = __version__) -> bool:
    """Return True if remote_tag is strictly newer than current_version."""
    return parse_version(remote_tag) > parse_version(current_version)


def check_for_updates(proxy: str | None = None) -> dict | None:
    """Query GitHub Releases API for the latest release metadata.

    Returns dict with {tag_name, body, html_url, installer_url, sha256_url, expected_sha256}
    or None if no release found or error.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"OpenDM-Updater/{__version__}",
    }
    handlers = [engine.SafeRedirectHandler()]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)

    req = urllib.request.Request(GITHUB_API_LATEST, headers=headers)
    try:
        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as ex:
        return {"error": str(ex)}

    tag_name = data.get("tag_name", "")
    body = data.get("body", "")
    html_url = data.get("html_url", "")

    installer_url = None
    sha256_url = None

    for asset in data.get("assets", []):
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if name.lower() == INSTALLER_ASSET_NAME.lower():
            installer_url = url
        elif name.lower() == SHA256_ASSET_NAME.lower():
            sha256_url = url

    expected_sha256 = None
    if sha256_url:
        expected_sha256 = fetch_expected_sha256(sha256_url, INSTALLER_ASSET_NAME, opener=opener)

    return {
        "tag_name": tag_name,
        "is_newer": is_newer_version(tag_name),
        "body": body,
        "html_url": html_url,
        "installer_url": installer_url,
        "sha256_url": sha256_url,
        "expected_sha256": expected_sha256,
    }


def fetch_expected_sha256(sha256_url: str, target_filename: str, opener=None) -> str | None:
    """Download SHA256SUMS.txt and extract 64-hex hash for target_filename.

    When no caller-supplied opener is given, builds one with
    engine.SafeRedirectHandler (never leaks Cookie/Authorization on
    cross-origin redirects) instead of using raw urllib.request.urlopen.
    """
    try:
        req = urllib.request.Request(sha256_url, headers={"User-Agent": f"OpenDM-Updater/{__version__}"})
        if opener is None:
            opener = urllib.request.build_opener(engine.SafeRedirectHandler())
        with opener.open(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
        return parse_sha256sums_text(text, target_filename)
    except Exception:
        return None


def parse_sha256sums_text(text: str, target_filename: str) -> str | None:
    """Parse SHA256SUMS format: <64-char-hex>  <filename>"""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            h, fn = parts[0], parts[-1]
            if fn.lstrip("*").lower() == target_filename.lower():
                if len(h) == 64 and all(c in "0123456789abcdefABCDEF" for c in h):
                    return h.lower()
    return None


def verify_installer(path: str | os.PathLike, expected_sha256: str | None) -> bool:
    """Verify a downloaded installer against its expected SHA-256 hash.

    Streams the file's SHA-256 and compares it to *expected_sha256* with
    hmac.compare_digest (constant-time comparison).

    Returns True on a match. Returns False -- never raises -- on a hash
    mismatch, a missing/unreadable file, or when no expected hash is
    available, so callers can delete the file and show an error instead
    of crashing.

    NOTE: a hash fetched over the same release channel as the installer
    proves INTEGRITY (the file arrived intact), not AUTHENTICITY. It does
    not defend against a compromised release channel; true authenticity
    requires a cryptographic signature over the release.
    """
    try:
        expected = expected_sha256.strip().lower()
    except AttributeError:
        return False
    if not expected or not os.path.isfile(path):
        return False
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return hmac.compare_digest(digest.hexdigest(), expected)


def get_update_target_path(tag_name: str) -> str:
    """Generate target installer file path in temp directory."""
    clean_tag = re.sub(r"[^\w.-]", "_", tag_name)
    return os.path.join(tempfile.gettempdir(), f"OpenDM-Setup-{clean_tag}.exe")


def execute_installer_and_exit(
    installer_path: str,
    on_before_exit: Callable[[], None] | None = None,
):
    """Safely spawn Windows installer as a detached process and terminate application."""
    if not os.path.exists(installer_path):
        raise FileNotFoundError(f"Installer not found at: {installer_path}")

    # Prepare detached process parameters
    cmd = [installer_path, "/SILENT", "/CLOSEAPPLICATIONS"]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )

    # Spawn installer detached
    subprocess.Popen(
        cmd,
        creationflags=creationflags,
        close_fds=True,
    )

    # Clean shutdown callback (stop daemon, flush queues)
    if on_before_exit:
        try:
            on_before_exit()
        except Exception:
            pass
