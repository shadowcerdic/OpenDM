"""Filename sanitization and Content-Disposition parser (RFC 6266 / RFC 5987)."""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

# Reserved on Windows (case-insensitive, even with extensions)
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Characters invalid on Windows and dangerous on POSIX
INVALID_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*\'#]')
MAX_FILENAME_LEN = 240


def parse_content_disposition(header: str | None) -> str | None:
    """Extract filename from Content-Disposition header.
    Prefers filename* (RFC 5987, UTF-8) over legacy filename parameter.
    """
    if not header:
        return None

    # 1. RFC 5987: filename*=UTF-8''<url-encoded>
    m = re.search(r"filename\*\s*=\s*([^;]+)", header, re.IGNORECASE)
    if m:
        raw = m.group(1).strip().strip('"\'')
        # format: charset'lang'encoded
        parts = raw.split("'", 2)
        if len(parts) == 3:
            charset, _lang, val = parts
            try:
                decoded = urllib.parse.unquote(val, encoding=charset or "utf-8")
                return decoded
            except Exception:
                pass
        else:
            try:
                return urllib.parse.unquote(raw, encoding="utf-8")
            except Exception:
                pass

    # 2. Legacy: filename="foo.bar" or filename=foo.bar
    m = re.search(r'filename\s*=\s*(?:"([^"]+)"|([^;\s]+))', header, re.IGNORECASE)
    if m:
        fn = m.group(1) or m.group(2)
        return fn.strip() if fn else None

    return None


def sanitize_filename(
    name: str | None,
    default: str = "download.bin",
    max_len: int = MAX_FILENAME_LEN,
) -> str:
    """Sanitize filename against path traversal, control chars, and OS restrictions.

    - strips path separators (/ and \)
    - decodes percent-encoded traversal like %2e%2e%2f
    - rejects / strips control characters and Windows-invalid chars (<>:"/\\|?*)
    - renames Windows-reserved names (CON -> _CON)
    - truncates base name if filename exceeds max_len (preserving extension)
    - strips leading/trailing dots and spaces
    - ensures non-empty return
    """
    if not name or not isinstance(name, str):
        return default

    # decode any URL encoding (guards against %2e%2e%2f or %00)
    try:
        name = urllib.parse.unquote(name)
    except Exception:
        pass

    # remove directory components (strip all path separators)
    name = re.sub(r"[/\\]+", "", name)

    # replace null and control characters with nothing
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)

    # replace forbidden filesystem characters with underscore
    name = INVALID_CHARS.sub("_", name)

    # collapse multiple dots and spaces at edges
    name = name.strip(". ")

    if not name:
        return default

    # handle Windows reserved basenames
    stem = name.split(".")[0].upper()
    if stem in WINDOWS_RESERVED:
        name = "_" + name

    # truncate if overly long while preserving extension
    if len(name) > max_len:
        if "." in name:
            stem, ext = name.rsplit(".", 1)
            ext = "." + ext[:20]
            stem = stem[: max_len - len(ext)]
            name = stem + ext
        else:
            name = name[:max_len]

    return name or default


def unique_destination(directory: str | Path, filename: str) -> Path:
    """Return non-colliding Path within directory, appending (1), (2), etc."""
    d = Path(directory)
    target = d / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    i = 1
    while True:
        candidate = d / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1
