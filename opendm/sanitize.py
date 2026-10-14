"""Filename sanitization and Content-Disposition parser (RFC 6266 / RFC 5987)."""
from __future__ import annotations

import os
import re
import unicodedata
import urllib.parse
from pathlib import Path

# Reserved on Windows (case-insensitive, even with extensions)
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Characters invalid on Windows and dangerous on POSIX.
# Apostrophe (') and '#' are legal in filenames and are intentionally kept.
INVALID_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
# Bidi / invisible characters used for spoofing (RLO attacks etc.).
# U+200C (ZWNJ) and U+200D (ZWJ) are deliberately NOT in this set: they are
# required for correct Persian/Indic spelling and are not spoofing vectors
# in the same way.
BIDI_CONTROLS = re.compile(
    "[\u202A-\u202E"  # LRE, RLE, PDF, LRO, RLO
    "\u2066-\u2069"  # LRI, RLI, FSI, PDI
    "\u200E\u200F"  # LRM, RLM
    "\u061C"  # ALM (Arabic letter mark)
    "\u200B"  # zero-width space
    "\u2060"  # word joiner
    "\uFEFF]"  # BOM / ZWNBSP
)
MAX_FILENAME_LEN = 240
MAX_FILENAME_BYTES = 255
# Windows MAX_PATH-style limit: 260 chars full path / 247 for dir+name.
# Only enforced on Windows (os.name == "nt").
WINDOWS_MAX_PATH_NAME_CHARS = 247


def _truncate_stem_chars(name: str, limit: int) -> str:
    """Truncate name to at most ``limit`` characters, preserving extension."""
    if len(name) <= limit:
        return name
    if "." in name and not name.startswith("."):
        stem, ext = name.rsplit(".", 1)
        ext = "." + ext[:20]
        stem = stem[: max(0, limit - len(ext))]
        return stem + ext
    return name[:limit]


def _truncate_stem_bytes(name: str, limit: int) -> str:
    """Truncate name so its UTF-8 encoding is at most ``limit`` bytes.

    Truncates the stem (preserving the extension) on a character boundary so
    no multi-byte character is split.
    """
    if len(name.encode("utf-8")) <= limit:
        return name
    if "." in name and not name.startswith("."):
        stem, ext = name.rsplit(".", 1)
        ext = "." + ext[:20]
    else:
        stem, ext = name, ""
    lo, hi = 0, len(stem)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len((stem[:mid] + ext).encode("utf-8")) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return stem[:lo] + ext


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
    r"""Sanitize filename against path traversal, control chars, and OS restrictions.

    - strips path separators (/ and \)
    - decodes percent-encoded traversal like %2e%2e%2f
    - strips bidi / invisible spoofing characters (RLO etc.), zero-width
      space, word joiner, BOM; keeps U+200C (ZWNJ) and U+200D (ZWJ)
    - NFC-normalizes the name
    - rejects / strips control characters and Windows-invalid chars (<>:"/\\|?*)
      while keeping legal ' (apostrophe) and #
    - renames Windows-reserved names (CON -> _CON)
    - truncates the stem (preserving extension) so the name fits max_len
      characters, the Windows MAX_PATH limit (Windows only), and
      MAX_FILENAME_BYTES UTF-8 bytes (all platforms)
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

    # strip bidi controls and invisible spoofing characters
    name = BIDI_CONTROLS.sub("", name)

    # normalize to composed form (canonical equivalent spellings unify)
    name = unicodedata.normalize("NFC", name)

    # remove null and control characters
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

    # truncate while preserving the extension
    name = _truncate_stem_chars(name, max_len)
    # Windows MAX_PATH-style guard (Windows only)
    if os.name == "nt" and len(name) > WINDOWS_MAX_PATH_NAME_CHARS:
        name = _truncate_stem_chars(name, WINDOWS_MAX_PATH_NAME_CHARS)
    # UTF-8 byte limit, enforced on all platforms
    name = _truncate_stem_bytes(name, MAX_FILENAME_BYTES)

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
