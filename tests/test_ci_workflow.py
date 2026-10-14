"""CI workflow static contract tests (spec item 22).

Validates .github/workflows/release.yml against the hardening rules:

  22a. SHA256SUMS generation must not use the buggy two-pattern ``-Filter``
       (PowerShell coerces ``-Filter "*.exe","*.zip"`` to the single string
       ``"*.exe *.zip"``, matching nothing -> empty SHA256SUMS.txt), and the
       step must FAIL the job if SHA256SUMS.txt is empty or lacks an entry
       for BOTH the installer exe and the extension zip.
  22b. Every third-party ``uses:`` must be pinned to a 40-hex commit SHA
       (human-readable tag kept in a trailing ``#`` comment).
  22c. Every ``pip install`` must pin exact versions with ``==``.
  22d. Downloads (embedded Python zip, get-pip.py) must be SHA-256 verified
       against pinned expected hashes before use, failing on mismatch.
  Extra static fix for "release has no installer": a pre-publish guard step
       must fail fast if any expected dist asset is missing/empty, before
       the release is created.

Stdlib-only so the test runs in CI with no new test dependencies.
"""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
INSTALLER_EXE = "OpenDM-Setup.exe"
EXTENSION_ZIP = "OpenDM-Chrome-Extension.zip"

def _text():
    assert WORKFLOW.is_file(), f"workflow file missing: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _uses_lines():
    """Return (uses_value, full_source_line) for every `uses:` line."""
    out = []
    for line in _text().splitlines():
        m = re.match(r"\s*uses:\s*([^\s#]+)", line)
        if m:
            out.append((m.group(1).strip("\"'"), line))
    return out


def _step_block(step_name):
    """Extract the raw YAML/text of the step with the given name."""
    text = _text()
    m = re.search(
        r"(?ms)^\s*-\s*name:\s*" + re.escape(step_name) + r"\s*\n(.*?)(?=^\s*-\s*name:|\Z)",
        text,
    )
    assert m, f"step not found: {step_name!r}"
    return m.group(0)


def test_yaml_parses():
    pytest.importorskip("yaml")
    import yaml as _yaml

    doc = _yaml.safe_load(_text())
    assert doc["name"]
    assert doc["jobs"]["build-release"]["runs-on"] == "windows-latest"


def test_22b_all_uses_pinned_to_commit_sha():
    entries = _uses_lines()
    assert entries, "no uses: lines found"
    bad = [u for u, _ in entries if not re.fullmatch(r"[^@/\s]+/[^@/\s]+@[0-9a-f]{40}", u)]
    assert not bad, f"tag-pinned (not SHA-pinned) actions: {bad}"


def test_22b_sha_pins_keep_human_readable_tag_comment():
    for uses, line in _uses_lines():
        assert "#" in line, f"SHA pin without human-readable tag comment: {line.strip()}"


def test_22a_no_two_pattern_filter():
    block = _step_block("Generate SHA-256 Checksums")
    # strip comment lines: the explanatory NOTE may quote the old buggy
    # invocation, but no *code* line may use a multi-pattern -Filter
    code = "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#")
    )
    assert '-Filter "*.exe", "*.zip"' not in code.replace("'", '"'), (
        "buggy two-pattern -Filter still present (silently yields nothing)"
    )
    assert not re.search(r'-Filter\s+"[^"]*",\s*"', code), (
        "multi-pattern -Filter still present"
    )


def test_22a_sha256sums_fail_guard():
    block = _step_block("Generate SHA-256 Checksums")
    # must enumerate both extensions (two Get-ChildItem calls, -Path array, or -Include)
    assert re.search(r"\*\.exe", block) and re.search(r"\*\.zip", block)
    # must fail the job on empty file or missing entries for both assets
    assert re.search(r"(?i)\bthrow\b|exit\s+1|::error", block), (
        "no fail-on-empty/missing-entry guard in SHA256SUMS step"
    )
    assert INSTALLER_EXE in block, "guard does not check the installer exe entry"
    assert EXTENSION_ZIP in block, "guard does not check the extension zip entry"


def test_22c_all_pip_installs_exact_pinned():
    bad = []
    for m in re.finditer(r"(?m)^\s*(?:&.*)?pip install\s+(.+?)\s*$", _text()):
        args = m.group(1)
        for tok in args.split():
            if tok.startswith("-") or tok == "pip":
                continue
            if "==" not in tok:
                bad.append(tok)
    assert not bad, f"unpinned pip packages: {bad}"


def test_22d_embedded_python_zip_hash_verified():
    text = _text()
    # pinned 64-hex expected hash for the embed zip (not a TODO placeholder)
    m = re.search(r"PYTHON_EMBED_SHA256\s*=\s*\"([0-9a-fA-F]{64})\"", text)
    assert m, "no pinned 64-hex PYTHON_EMBED_SHA256 found"
    assert "TODO" not in m.group(0)
    # verified with Get-FileHash BEFORE Expand-Archive, failing on mismatch
    dl = _step_block("Prepare Staged Windows Runtime & Payload")
    assert "Get-FileHash" in dl and "PYTHON_EMBED_SHA256" in dl
    assert dl.index("Get-FileHash") < dl.index("Expand-Archive"), (
        "hash must be verified before extraction"
    )
    assert re.search(r"(?i)\bthrow\b", dl), "no fail-on-mismatch for the embed zip"


def test_22d_get_pip_hash_verified():
    dl = _step_block("Prepare Staged Windows Runtime & Payload")
    assert "get-pip.py" in dl
    # a SHA-256 verification block must exist for get-pip.py and fail on mismatch.
    # The expected value may be the explicitly-marked TODO placeholder (spec 22d
    # allows this when no officially published hash exists), but the check itself
    # must be present and enforced.
    assert re.search(r"GET_PIP_SHA256", dl), "no get-pip.py hash variable"
    assert "Get-FileHash" in dl
    assert re.search(r"(?i)\bthrow\b", dl)


def test_prepublish_asset_guard_exists():
    text = _text()
    pub = text.index("Publish GitHub Release")
    guard = re.search(
        r"(?ms)^\s*-\s*name:\s*Verify Release Assets.*?(?=^\s*-\s*name:)",
        text,
    )
    assert guard, "no pre-publish asset guard step before the release"
    assert text.index(guard.group(0)) < pub, "asset guard must run before Publish"
    block = guard.group(0)
    assert INSTALLER_EXE in block and EXTENSION_ZIP in block and "SHA256SUMS" in block
    assert re.search(r"(?i)\bthrow\b|exit\s+1", block), "asset guard does not fail the job"
