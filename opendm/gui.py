"""OpenDM desktop GUI (PySide6)."""
from __future__ import annotations

import os
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QColor, QIcon, QPainter, QFont,
                           QKeySequence, QShortcut, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget,
    QTreeWidgetItem, QPushButton, QLabel, QMenu, QMenuBar, QLineEdit,
    QComboBox, QSpinBox, QDialog, QProgressBar, QToolBar, QStatusBar,
    QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QMessageBox,
    QFileDialog, QFontDialog, QDialogButtonBox, QTabWidget, QPlainTextEdit,
    QGroupBox, QSlider, QCheckBox, QTableWidgetItem, QTableWidget,
)
from http.server import HTTPServer

from .queue import QueueManager, STATES

APP_NAME = "OpenDM"
DOWNLOADS_DIR = os.environ.get("OPENDM_HOME", "/root/Downloads") if os.name == "posix" else os.path.join(os.path.expanduser("~"), "Downloads")


# ---------------------------------------------------------------- widgets
class ProgressDelegate(QStyledItemDelegate):
    """Progress bar with connection segment blocks rendered inline."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        job = index.data(Qt.UserRole)
        if job is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(4, option.rect.height() // 2 - 7,
                                    -4, -(option.rect.height() // 2 - 7))
        # trough
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2a2f3a"))
        painter.drawRoundedRect(rect, 4, 4)
        w = rect.width()
        if job.total:
            for part in job.parts:
                x0 = rect.left() + part["start"] * w // job.total
                x1 = rect.left() + (part["start"] + part.get("have", 0)) * w // job.total
                if x1 > x0:
                    c = QColor("#3d7eff") if job.state == "downloading" else QColor("#2eaa6a")
                    painter.setBrush(c)
                    painter.drawRoundedRect(int(x0), rect.top(), int(x1 - x0), rect.height(), 3, 3)
        painter.restore()
        # percent text
        painter.save()
        painter.setPen(QColor("#c8d0dc"))
        f = QFont(option.font)
        f.setPointSize(7)
        painter.setFont(f)
        pct = f"{job.done * 100 // job.total}%" if job.total else "…"
        painter.drawText(option.rect.adjusted(6, -14, -6, 14),
                         Qt.AlignRight | Qt.AlignVCenter, pct)
        painter.restore()


class AddUrlDialog(QDialog):
    def __init__(self, parent=None, default_url=""):
        super().__init__(parent)
        self.setWindowTitle("Add URL — OpenDM")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("URL:"))
        self.url = QLineEdit(default_url)
        self.url.setPlaceholderText("https://example.com/file.zip")
        lay.addWidget(self.url)
        row = QHBoxLayout()
        row.addWidget(QLabel("Connections:"))
        self.conns = QSpinBox()
        self.conns.setRange(1, 32)
        self.conns.setValue(4)
        row.addWidget(self.conns)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class DetailsDialog(QDialog):
    """Per-file progress window: segmented connection map showing each
    connection's byte window and how much of it is filled."""

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Downloading — {job.filename}")
        self.resize(560, 300)
        self.job = job
        lay = QVBoxLayout(self)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        lay.addWidget(self.info)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(400)
        self.refresh()

    def refresh(self):
        j = self.job
        mb = 1024 * 1024
        lines = [
            f"URL:       {j.url}",
            f"File:      {j.filename}   [{j.category}]",
            f"State:     {j.state}",
            f"Progress:  {j.done / mb:.2f} / {j.total / mb:.2f} MB "
            f"({j.done * 100 // j.total if j.total else 0}%)",
            f"Speed:     {j.speed_avg / mb:.2f} MB/s   connections: {j.conns}",
            "",
            "Per-connection byte windows and progress:",
        ]
        for i, p in enumerate(j.parts):
            got = p.get("have", 0)
            span = p["end"] - p["start"] + 1
            bar = "█" * int(got * 24 / max(span, 1)) + "·" * (24 - int(got * 24 / max(span, 1)))
            lines.append(f"  conn {i}:  {p['start']:>10,} … {p['end']:>10,}   "
                         f"{bar}  {got * 100 // max(span, 1):>3}%")
        if j.sha256:
            lines += ["", f"SHA-256:   {j.sha256}"]
        if j.error:
            lines += ["", f"Error: {j.error}"]
        self.info.setPlainText("\n".join(lines))


# ------------------------------------------------------------------ main
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — open download manager")
        self.resize(1000, 620)
        dest = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(dest, exist_ok=True)
        self.qm = QueueManager(dest)

        # Embedded daemon: serve the extension API while the app runs
        self._daemon_thread = None
        self._start_embedded_daemon()

        self._build_menu()
        self._build_toolbar()
        self._build_tree()
        self._build_status()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_rows)
        self.timer.start(400)

    def _start_embedded_daemon(self):
        """Run the hardened HTTP API inside the GUI process (port 10010).
        If the port is already served (standalone daemon), skip silently."""
        try:
            from . import daemon as daemon_mod
            daemon_mod.QM = self.qm  # share the same queue
            httpd = HTTPServer(("127.0.0.1", daemon_mod.HTTP_PORT),
                               daemon_mod.HardenedApiHandler)
            self._daemon_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._daemon_thread.start()
        except OSError:
            pass  # standalone daemon already listening
        except Exception:
            pass

    # ---- UI construction ------------------------------------------------
    def _build_menu(self):
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        for label, slot, shortcut in [
            ("Add URL…", self.add_url, "Ctrl+N"),
            ("Start all", self.start_all, "Ctrl+S"),
            ("Exit", self.close, "Ctrl+Q"),
        ]:
            a = QAction(label, self)
            a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            m_file.addAction(a)

        m_job = mb.addMenu("&Job")
        for label, slot in [
            ("Pause", self.pause_selected), ("Resume", self.resume_selected),
            ("Cancel", self.cancel_selected), ("Details…", self.details_selected),
        ]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            m_job.addAction(a)

        m_help = mb.addMenu("&Help")
        a = QAction("About OpenDM", self)
        a.triggered.connect(self.about)
        m_help.addAction(a)

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        for label, slot in [
            ("＋ Add", self.add_url), ("▶ Start All", self.start_all),
            ("⏸ Pause", self.pause_selected), ("↻ Resume", self.resume_selected),
            ("✖ Cancel", self.cancel_selected), ("📋 Details", self.details_selected),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            tb.addWidget(b)
        self.addToolBar(tb)

    def _build_tree(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["File", "Size", "Progress", "Speed", "State", "Category", "SHA-256"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 200)
        self.tree.setColumnWidth(3, 110)
        self.tree.setColumnWidth(4, 100)
        self.tree.setColumnWidth(5, 90)
        self.tree.setColumnWidth(6, 130)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self.details_item)
        self.setCentralWidget(self.tree)

    def _build_status(self):
        sb = self.statusBar()
        self.stat_label = QLabel("Ready")
        sb.addWidget(self.stat_label)

    # ---- actions --------------------------------------------------------
    def add_url(self):
        dlg = AddUrlDialog(self)
        if dlg.exec() and dlg.url.text().strip():
            job = self.qm.add(dlg.url.text().strip(), conns=dlg.conns.value())
            job.start()
            self.refresh_rows()

    def start_all(self):
        self.qm.start_all()

    def _selected_job(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def pause_selected(self):
        j = self._selected_job()
        if j:
            j.pause()

    def resume_selected(self):
        j = self._selected_job()
        if j and j.state in ("paused", "error"):
            j.start()

    def cancel_selected(self):
        j = self._selected_job()
        if j:
            j.cancel()

    def details_selected(self):
        j = self._selected_job()
        if j:
            DetailsDialog(j, self).exec()

    def details_item(self, item, _col):
        j = item.data(0, Qt.UserRole)
        if j:
            DetailsDialog(j, self).exec()

    def about(self):
        QMessageBox.about(
            self, "About OpenDM",
            "<b>OpenDM 1.0</b><br>Independent open-source implementation of a classic "
            "download-manager architecture: multi-connection ranged downloads, "
            "per-connection part files, resume, final assembly, SHA-256 proof.<br>"
            "<br>GPL-3.0 — reconstructed with REA-assisted reverse engineering. "
            "Not affiliated with Tonec FZE.")

    # ---- refresh ---------------------------------------------------------
    def refresh_rows(self):
        jobs = list(self.qm.jobs)
        self.tree.clear()
        mb = 1024 * 1024
        for j in jobs:
            it = QTreeWidgetItem([
                j.filename,
                f"{j.total / mb:.1f} MB" if j.total else "—",
                "",
                f"{j.speed_avg / mb:.2f} MB/s" if j.state == "downloading" else "",
                j.state,
                j.category,
                j.sha256[:12] + "…" if j.sha256 else "",
            ])
            it.setData(2, Qt.UserRole, j)
            self.tree.addTopLevelItem(it)
        self.tree.setItemDelegateForColumn(2, ProgressDelegate(self.tree))
        active = sum(1 for j in jobs if j.state == "downloading")
        done = sum(1 for j in jobs if j.state == "completed")
        self.stat_label.setText(f"{len(jobs)} jobs — {active} downloading — {done} completed")


DARK_QSS = """
QMainWindow, QDialog { background: #1b2029; }
QToolBar { background: #232a36; border: none; padding: 4px; spacing: 6px; }
QPushButton { background: #2c3442; color: #d7dee9; border: 1px solid #3a4456; border-radius: 5px; padding: 5px 12px; }
QPushButton:hover { background: #35405280; }
QTreeWidget { background: #20262f; color: #cfd6e2; border: 1px solid #2c3442; font-size: 13px; }
QHeaderView::section { background: #232a36; color: #9fb0c8; border: none; padding: 6px; }
QStatusBar { background: #1b2029; color: #8fa1bb; }
QPlainTextEdit { background: #20262f; color: #cfd6e2; font-family: monospace; font-size: 12px; }
QMenu { background: #232a36; color: #d7dee9; }
QLabel, QComboBox, QSpinBox { color: #d7dee9; }
QLineEdit { background: #20262f; color: #d7dee9; border: 1px solid #3a4456; padding: 4px; }
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
