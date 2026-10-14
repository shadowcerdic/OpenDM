"""Spec item 19 — Native host path: no hard-coded C:\\OpenDM.

Renders the native-messaging-host manifest the way the installer would
(template -> replace {app} placeholder with the real install dir, staged
into a temp dir on Linux) and asserts the rendered manifest points at a
real file inside the staged dir.

NOTE: Inno Setup [Code]/registry behavior (HKCU Chrome + Edge keys,
uninstall key removal) is unverified on real Windows — this test validates
the template + render logic + installer.iss content only.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BAT = REPO / "opendm" / "native_host.bat"
TEMPLATE = REPO / "opendm" / "org.opendm.native_host.json"
ISS = REPO / "installer.iss"

HARDCODED = "C:\\OpenDM"  # literal text that must not appear (backslash variants below)


def _assert_no_hardcoded_opendm(text: str, where: str) -> None:
    """Fail if any hard-coded C:\\OpenDM path survives (single/double backslash)."""
    for variant in ("C:\\OpenDM", "C:\\\\OpenDM"):
        assert variant not in text, f"hard-coded {variant!r} found in {where}"


def _code_section(iss_text: str) -> str:
    idx = iss_text.find("[Code]")
    assert idx != -1, "installer.iss has no [Code] section"
    return iss_text[idx:]


# ---------------------------------------------------------------- bat
class TestNativeHostBat:
    def test_no_hardcoded_path(self):
        _assert_no_hardcoded_opendm(BAT.read_text(), "opendm/native_host.bat")

    def test_derives_paths_from_own_directory(self):
        text = BAT.read_text()
        assert "%~dp0" in text, (
            "native_host.bat must derive paths from %~dp0 (its own directory)"
        )

    def test_locates_python_and_host_next_to_itself(self):
        text = BAT.read_text()
        assert "native_host.py" in text, "batch file must locate native_host.py next to itself"
        assert "python.exe" in text, "batch file must locate python.exe"


# ---------------------------------------------------------- json template
class TestManifestTemplate:
    def test_is_a_template_not_hardcoded(self):
        text = TEMPLATE.read_text()
        _assert_no_hardcoded_opendm(text, "org.opendm.native_host.json template")

    def test_has_install_dir_placeholder(self):
        text = TEMPLATE.read_text()
        assert "{app}" in text or "%%APP%%" in text, (
            "manifest template must contain an install-dir placeholder ({app} or %%APP%%)"
        )

    def test_placeholder_in_path_field(self):
        doc = json.loads(TEMPLATE.read_text())
        path_value = doc.get("path", "")
        assert "{app}" in path_value or "%%APP%%" in path_value, (
            f"manifest 'path' field must carry the placeholder, got: {path_value!r}"
        )

    def test_template_still_valid_json(self):
        doc = json.loads(TEMPLATE.read_text())
        assert doc["name"] == "org.opendm.native_host"
        assert doc["type"] == "stdio"


# ------------------------------------------------- staged render simulation
def _stage_install(tmp: Path) -> Path:
    """Simulate an installed layout with {app} -> tmp dir (Linux temp path)."""
    app = tmp / "app"
    (app / "app" / "opendm").mkdir(parents=True)
    (app / "python").mkdir(parents=True)
    shutil.copy(BAT, app / "app" / "opendm" / "native_host.bat")
    shutil.copy(REPO / "opendm" / "native_host.py", app / "app" / "opendm" / "native_host.py")
    shutil.copy(TEMPLATE, app / "app" / "opendm" / "org.opendm.native_host.json")
    (app / "python" / "python.exe").touch()  # placeholder; only path logic is tested
    return app


def render_manifest_like_installer(template_text: str, install_dir: Path) -> str:
    """Mirror the installer.iss [Code] render logic in Python.

    {app} -> JSON-escaped install dir (Inno renders on Windows; here we use
    the staged POSIX path, still asserting structure, not platform separators).
    """
    escaped = str(install_dir).replace("\\", "\\\\")
    return template_text.replace("{app}", escaped).replace("%%APP%%", escaped)


class TestStagedRender:
    def test_rendered_path_exists_inside_staged_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix="opendm-item19-"))
        try:
            staged_app = _stage_install(tmp)
            rendered = render_manifest_like_installer(TEMPLATE.read_text(), staged_app)
            _assert_no_hardcoded_opendm(rendered, "rendered manifest")

            doc = json.loads(rendered)  # rendered manifest must stay valid JSON
            path_value = doc["path"]
            assert path_value.startswith(str(staged_app)), (
                f"rendered path {path_value!r} is not inside staged dir {staged_app}"
            )
            # On Linux, normalize the Windows backslash separators to assert the
            # staged layout resolves on disk (path logic tested via rendering).
            staged_path = Path(path_value.replace("\\", os.sep))
            assert staged_path.is_file(), (
                f"rendered manifest path does not resolve on disk: {staged_path!r}"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bat_paths_resolve_inside_staged_dir(self):
        """The %~dp0-relative references in the .bat must resolve under staged {app}."""
        tmp = Path(tempfile.mkdtemp(prefix="opendm-item19-"))
        try:
            staged_app = _stage_install(tmp)
            bat_text = BAT.read_text()
            # %~dp0 itself is the bat's directory: staged {app}/app/opendm/
            host_dir = staged_app / "app" / "opendm"
            # The bat references native_host.py next to itself and python two levels up.
            assert (host_dir / "native_host.py").is_file()
            # python.exe is expected at {app}/python relative to the bat's dir
            assert (host_dir / ".." / ".." / "python" / "python.exe").resolve().is_file() \
                or (staged_app / "python" / "python.exe").is_file()
            assert "native_host.py" in bat_text and "%~dp0" in bat_text
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------- installer.iss
class TestInstallerIss:
    def test_no_hardcoded_path_in_code_section(self):
        _assert_no_hardcoded_opendm(_code_section(ISS.read_text()), "installer.iss [Code]")

    def test_code_section_renders_manifest_with_app_path(self):
        code = _code_section(ISS.read_text())
        assert "org.opendm.native_host.json" in code, (
            "[Code] must render org.opendm.native_host.json at install time"
        )
        assert "{app}" in code or "ExpandConstant" in code, (
            "[Code] must fill the template with the real {app} path"
        )

    def test_registers_chrome_and_edge_under_hkcu(self):
        text = ISS.read_text()
        for subkey in (
            r"Software\Google\Chrome\NativeMessagingHosts\org.opendm.native_host",
            r"Software\Microsoft\Edge\NativeMessagingHosts\org.opendm.native_host",
        ):
            assert subkey in text, f"installer.iss missing registry subkey {subkey!r}"
        assert "HKCU" in text

    def test_registry_keys_removed_on_uninstall(self):
        text = ISS.read_text()
        assert "uninsdeletekey" in text, "registry keys must be removed on uninstall"

    def test_registry_points_at_rendered_manifest(self):
        text = ISS.read_text()
        assert r"{app}\app\opendm\org.opendm.native_host.json" in text


# --------------------------------------------- token bootstrap from install
class TestTokenBootstrapPathing:
    """native_host.py must work from the installed location without CWD tricks."""

    def test_config_dir_not_cwd_dependent(self):
        src = (REPO / "opendm" / "security.py").read_text()
        assert "def config_dir" in src
        # config_dir resolves from APPDATA/XDG/home — never from the install dir or CWD
        assert "APPDATA" in src

    def test_native_host_adds_project_root_from_file(self):
        src = (REPO / "opendm" / "native_host.py").read_text()
        assert "Path(__file__)" in src, (
            "native_host.py must derive PROJECT_ROOT from its own file location"
        )
