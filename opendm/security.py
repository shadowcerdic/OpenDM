"""OpenDM configuration and security helpers."""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

DEFAULT_HTTP_PORT = 10010
DEFAULT_WS_PORT = 10011
ALLOWED_HOSTS = {
    f"127.0.0.1:{DEFAULT_HTTP_PORT}",
    f"localhost:{DEFAULT_HTTP_PORT}",
    "127.0.0.1",
    "localhost",
}


def config_dir(app: str = "opendm") -> Path:
    """Return the per-user config directory for an OpenDM app.

    POSIX: $XDG_CONFIG_HOME/<app> (or ~/.config/<app>). Windows
    (sys.platform == "win32"): %APPDATA%\\OpenDM. The Windows path is
    guarded by sys.platform so it can be exercised with mocks on Linux;
    it is unverified on real Windows.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OpenDM"
    base = os.environ.get("XDG_CONFIG_HOME")
    p = Path(base) if base else Path.home() / ".config"
    return p / app


def config_path() -> Path:
    return config_dir() / "daemon.json"


def load_or_create_config() -> dict:
    """Load ~/.config/opendm/daemon.json; create with 0600 perms if missing."""
    cd = config_dir()
    cd.mkdir(parents=True, exist_ok=True)
    cp = config_path()
    if cp.exists():
        try:
            with open(cp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "token" in data and data["token"]:
                return data
        except Exception:
            pass
    token = secrets.token_hex(32)
    data = {
        "token": token,
        "http_port": DEFAULT_HTTP_PORT,
        "ws_port": DEFAULT_WS_PORT,
        "download_dir": str(Path.home() / "Downloads"),
        "allowed_origins": [
            "chrome-extension://hnhmponoadleelhnecpijhnlpefkjflc",
        ],
    }
    _write_config(data)
    return data


def _write_config(data: dict) -> None:
    """Atomically replace daemon.json, mode 0600. Preserves every key in data."""
    cp = config_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, cp)
    try:
        os.chmod(cp, 0o600)
    except Exception:
        pass


_SPEED_LIMIT_MB_MAX = 100


def load_ui_settings() -> dict:
    """Persisted GUI prefs. Missing keys use the same defaults as a fresh install."""
    cfg = load_or_create_config()
    proxy = cfg.get("proxy") or ""
    if not isinstance(proxy, str):
        proxy = ""
    auto = cfg.get("auto_check_updates", True)
    if not isinstance(auto, bool):
        auto = True
    try:
        speed = int(cfg.get("speed_limit_mb") or 0)
    except (TypeError, ValueError):
        speed = 0
    return {
        "proxy": proxy,
        "auto_check_updates": auto,
        "speed_limit_mb": max(0, min(_SPEED_LIMIT_MB_MAX, speed)),
    }


def save_ui_settings(
    *,
    proxy: str | None = None,
    auto_check_updates: bool | None = None,
    speed_limit_mb: int | None = None,
) -> dict:
    """Merge UI prefs into daemon.json. Does not rotate the session token.

    None leaves that key unchanged. speed_limit_mb is clamped to 0..100
    (the slider range; 0 = unlimited).
    """
    cfg = load_or_create_config()
    if proxy is not None:
        cfg["proxy"] = proxy if isinstance(proxy, str) else ""
    if auto_check_updates is not None:
        cfg["auto_check_updates"] = bool(auto_check_updates)
    if speed_limit_mb is not None:
        try:
            speed = int(speed_limit_mb)
        except (TypeError, ValueError):
            speed = 0
        cfg["speed_limit_mb"] = max(0, min(_SPEED_LIMIT_MB_MAX, speed))
    _write_config(cfg)
    return cfg


def origin_allowed(origin: str | None, patterns: list[str]) -> bool:
    """Check if Origin header matches one of the allowed patterns."""
    if not origin:
        return False
    origin = origin.strip().lower()
    for pat in patterns:
        pat = pat.strip().lower()
        if pat == "*":
            return False  # explicit rejection of wildcard
        if pat.endswith("*") and origin.startswith(pat[:-1]):
            return True
        if origin == pat:
            return True
    return False


def host_allowed(host: str | None, allowed: set[str] | None = None) -> bool:
    """Validate Host header against loopback whitelist (DNS rebinding guard).
    Accepts 127.0.0.1:<any-port>, localhost:<any-port>, or bare 127.0.0.1/localhost.
    """
    if not host:
        return False
    host = host.strip().lower()
    if allowed and host in allowed:
        return True
    hostname = host.split(":", 1)[0]
    return hostname in ("127.0.0.1", "localhost")
