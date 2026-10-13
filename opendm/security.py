"""OpenDM configuration and security helpers."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

DEFAULT_HTTP_PORT = 10010
DEFAULT_WS_PORT = 10011
ALLOWED_HOSTS = {
    f"127.0.0.1:{DEFAULT_HTTP_PORT}",
    f"localhost:{DEFAULT_HTTP_PORT}",
    "127.0.0.1",
    "localhost",
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    p = Path(base) if base else Path.home() / ".config"
    return p / "opendm"


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
            "chrome-extension://*",
            "moz-extension://*",
        ],
    }
    tmp = cp.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, cp)
    try:
        os.chmod(cp, 0o600)
    except Exception:
        pass
    return data


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
