"""GUI wiring tests for spec items 13, 15, 16 (opendm/gui.py).

Headless: run with QT_QPA_PLATFORM=offscreen. Requires PySide6 + system
libEGL. If Qt cannot initialize in an environment, the module-level helpers
(update_sha_offer_line / update_no_hash_notice_line) and the source-level
regression guards still exercise the non-widget logic; see the module
docstring note in the final report.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QLabel

from opendm import gui


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def win(qapp, monkeypatch):
    """A MainWindow whose embedded daemon startup is stubbed out so tests
    never bind a real port. The original is kept as
    win._real_start_embedded_daemon for the daemon-specific test."""
    orig = gui.MainWindow._start_embedded_daemon
    monkeypatch.setattr(gui.MainWindow, "_start_embedded_daemon", lambda self: None)
    w = gui.MainWindow()
    w._real_start_embedded_daemon = orig.__get__(w)
    yield w
    w.close()


class FakeJob:
    """Minimal stand-in for queue.Job: records attribute assignment and
    completion-callback registration."""

    def __init__(self):
        object.__setattr__(self, "assigned", {})
        object.__setattr__(self, "completed_cbs", [])

    def __setattr__(self, name, value):
        if name in ("assigned", "completed_cbs"):
            object.__setattr__(self, name, value)
        else:
            self.assigned[name] = value
            object.__setattr__(self, name, value)

    def add_on_complete(self, fn):
        self.completed_cbs.append(fn)

    def start(self):
        pass


@pytest.fixture()
def fake_qm():
    class _QM:
        def __init__(self):
            self.job = FakeJob()
            self.set_speed_limit_calls = []

        def add(self, **kwargs):
            return self.job

        def set_speed_limit(self, bps):
            self.set_speed_limit_calls.append(bps)

    return _QM()


def make_info(**over):
    info = {
        "tag_name": "v9.9.9",
        "is_newer": True,
        "body": "notes",
        "html_url": "https://github.com/example/opendm/releases/tag/v9.9.9",
        "installer_url": "https://github.com/example/opendm/releases/download/v9.9.9/OpenDM-Setup.exe",
        "sha256_url": None,
        "expected_sha256": "ab" * 32,
    }
    info.update(over)
    return info


# ---------------------------------------------------------------------------
# Item 13 — speed-limit slider wiring
# ---------------------------------------------------------------------------
def test_speed_slider_calls_qm_set_speed_limit(win, fake_qm):
    win.qm = fake_qm
    win._on_speed_slider_changed(10)
    assert fake_qm.set_speed_limit_calls == [10 * 1024 * 1024]
    assert win.speed_slider_lbl.text() == "10 MB/s"


def test_speed_slider_zero_means_unlimited(win, fake_qm):
    win.qm = fake_qm
    win._on_speed_slider_changed(0)
    assert fake_qm.set_speed_limit_calls == [0]
    assert win.speed_slider_lbl.text() == "Unlimited"


def test_no_dead_job_speed_limit_block():
    src = open(os.path.join(os.path.dirname(gui.__file__), "gui.py"), encoding="utf-8").read()
    assert 'hasattr(job, "speed_limit")' not in src
    assert "job.speed_limit" not in src


# ---------------------------------------------------------------------------
# Item 15 — update flow
# ---------------------------------------------------------------------------
def test_sha_offer_line_never_claims_verified():
    line = gui.update_sha_offer_line("ab" * 32)
    assert "Verified" not in line and "✓" not in line
    assert "Expected SHA-256" in line
    assert line.startswith("Expected SHA-256: abababababababab")


def test_sha_offer_line_without_hash():
    line = gui.update_sha_offer_line(None)
    assert "Verified" not in line and "✓" not in line
    assert "No SHA-256 checksum" in line


def test_no_premature_verified_claim_in_source():
    src = open(os.path.join(os.path.dirname(gui.__file__), "gui.py"), encoding="utf-8").read()
    assert "✓ Verified" not in src
    assert "Verified SHA-256 Digest" not in src


def test_offer_dialog_with_hash_has_install_button(win):
    dlg = win._build_update_offer_dialog(make_info())
    try:
        install = dlg.findChild(QPushButton, "install_btn")
        assert install is not None and install.text() == "Download & Update Now"
        labels = [l.text() for l in dlg.findChildren(QLabel)]
        assert any("Expected SHA-256" in t for t in labels)
        assert not any("Verified" in t for t in labels)
        assert not any("github.com/example" in t for t in labels)  # URL only when no hash
    finally:
        dlg.close()


def test_offer_dialog_without_hash_shows_release_url_no_install(win):
    dlg = win._build_update_offer_dialog(make_info(expected_sha256=None))
    try:
        assert dlg.findChild(QPushButton, "install_btn") is None
        labels = [l.text() for l in dlg.findChildren(QLabel)]
        assert any("https://github.com/example/opendm/releases/tag/v9.9.9" in t for t in labels)
        assert not any("Verified" in t for t in labels)
    finally:
        dlg.close()


def test_start_update_download_registers_on_complete_not_post_finish(win, fake_qm, monkeypatch):
    win.qm = fake_qm
    win.refresh_rows = lambda *a, **k: None
    monkeypatch.setattr(gui.QMessageBox, "information", lambda *a, **k: None)

    finished = []

    def fake_finish(job, target_path, tag, expected_sha):
        finished.append((target_path, tag, expected_sha))

    monkeypatch.setattr(win, "_finish_update_download", fake_finish)
    # QTimer.singleShot(0, fn): run fn immediately in the test
    monkeypatch.setattr(gui.QTimer, "singleShot", lambda ms, fn: fn())

    win._start_update_download("https://x/y.exe", "v9.9.9", "ab" * 32)

    job = fake_qm.job
    assert "post_finish" not in job.assigned  # dead path removed
    assert len(job.completed_cbs) == 1
    assert job.assigned.get("expected_sha256") == "ab" * 32

    # The registered callback marshals to the GUI thread (singleShot 0) and
    # never touches widgets itself.
    assert len(finished) == 0  # not yet invoked
    job.completed_cbs[0](job)
    assert len(finished) == 1
    target_path, tag, sha = finished[0]
    assert tag == "v9.9.9" and sha == "ab" * 32 and target_path.endswith(".exe")


def test_finish_verified_true_offers_install(win, tmp_path, monkeypatch):
    target = tmp_path / "OpenDM-Setup-v9.9.9.exe"
    target.write_bytes(b"installer-bytes")
    monkeypatch.setattr("opendm.updater.verify_installer", lambda p, h: True)
    asked = []
    monkeypatch.setattr(
        gui.QMessageBox, "question",
        lambda *a, **k: (asked.append(a), gui.QMessageBox.Yes)[1],
    )
    critical = []
    monkeypatch.setattr(gui.QMessageBox, "critical", lambda *a, **k: critical.append(a))
    launched = []
    monkeypatch.setattr(win, "_launch_installer_and_quit", lambda p: launched.append(p))

    win._finish_update_download(None, str(target), "v9.9.9", "ab" * 32)

    assert len(asked) == 1
    assert "verified successfully" in asked[0][2]
    assert launched == [str(target)]
    assert critical == []
    assert target.exists()  # not deleted


def test_finish_verified_false_deletes_and_errors_no_launch(win, tmp_path, monkeypatch):
    target = tmp_path / "OpenDM-Setup-v9.9.9.exe"
    target.write_bytes(b"installer-bytes")
    seen = []

    def fake_verify(path, expected):
        seen.append((path, expected))
        return False

    monkeypatch.setattr("opendm.updater.verify_installer", fake_verify)
    asked = []
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *a, **k: asked.append(a))
    critical = []
    monkeypatch.setattr(gui.QMessageBox, "critical", lambda *a, **k: critical.append(a))
    launched = []
    monkeypatch.setattr(win, "_launch_installer_and_quit", lambda p: launched.append(p))

    win._finish_update_download(None, str(target), "v9.9.9", "ab" * 32)

    assert seen == [(str(target), "ab" * 32)]
    assert not target.exists()  # deleted
    assert len(critical) == 1
    assert "NOT be launched" in critical[0][2]
    assert asked == []  # no install prompt
    assert launched == []  # installer NOT launched


def test_finish_none_hash_deletes_and_errors(win, tmp_path, monkeypatch):
    target = tmp_path / "OpenDM-Setup-v9.9.9.exe"
    target.write_bytes(b"installer-bytes")
    monkeypatch.setattr("opendm.updater.verify_installer", lambda p, h: False)
    monkeypatch.setattr(gui.QMessageBox, "critical", lambda *a, **k: None)
    launched = []
    monkeypatch.setattr(win, "_launch_installer_and_quit", lambda p: launched.append(p))

    win._finish_update_download(None, str(target), "v9.9.9", None)

    assert not target.exists()
    assert launched == []


# ---------------------------------------------------------------------------
# Item 16 — embedded daemon
# ---------------------------------------------------------------------------
def test_daemon_startup_uses_threaded_server_and_logs_errors(win, monkeypatch, capsys):
    import opendm.daemon as daemon_mod

    used = []

    class BoomServer:
        def __init__(self, addr, handler):
            used.append((addr, handler))
            raise RuntimeError("OpenDM daemon: cannot bind 127.0.0.1:10010 (port already in use?)")

    monkeypatch.setattr(daemon_mod, "ThreadedHTTPServer", BoomServer)
    win._real_start_embedded_daemon()  # must not raise
    assert used and used[0][0] == ("127.0.0.1", daemon_mod.HTTP_PORT)
    assert used[0][1] is daemon_mod.HardenedApiHandler
    err = capsys.readouterr().err
    assert "embedded daemon failed to start" in err
    assert "cannot bind" in err


def test_no_plain_httpserver_import_in_source():
    src = open(os.path.join(os.path.dirname(gui.__file__), "gui.py"), encoding="utf-8").read()
    assert "from http.server import HTTPServer" not in src
    assert "HTTPServer(" not in src.replace("ThreadedHTTPServer(", "")
