"""OpenDM modern desktop GUI (PySide6) — Professional & Polished.

Features:
- Modern sleek dark theme (Fluent / Modern desktop inspired)
- Left sidebar with navigation categories & status filters
- Crisp vector-drawn icons (no external asset dependencies)
- Interactive context menus (right-click on rows)
- Custom smooth progress bar delegate with connection slice visualization
- Details modal with real-time per-connection visual segment map
- Download Confirmation Prompt (Interactive popup on intercepted download)
- Minimize to System Tray support with tray menu & background execution
- Auto-retry network resiliency in engine
- Embedded secure daemon
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize, QRectF, Signal, QObject
from PySide6.QtGui import (
    QAction, QColor, QIcon, QPainter, QFont, QPen, QBrush,
    QKeySequence, QPixmap, QPainterPath
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton, QLabel, QMenu, QMenuBar,
    QLineEdit, QComboBox, QSpinBox, QDialog, QToolBar, QStatusBar,
    QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QMessageBox,
    QFileDialog, QDialogButtonBox, QPlainTextEdit, QFrame, QSplitter,
    QSystemTrayIcon
)
from http.server import HTTPServer

from .queue import QueueManager, STATES
from .sanitize import sanitize_filename

APP_NAME = "OpenDM"
DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")


# -----------------------------------------------------------------------------
# Dynamic Vector Icon Generator (clean, hi-dpi, no missing unicode glyphs)
# -----------------------------------------------------------------------------
def make_icon(icon_type: str, color: str = "#e2e8f0", size: int = 24) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)

    if icon_type == "add":
        painter.drawLine(12, 5, 12, 19)
        painter.drawLine(5, 12, 19, 12)
    elif icon_type == "play":
        painter.setBrush(QColor(color))
        path = QPainterPath()
        path.moveTo(8, 6)
        path.lineTo(18, 12)
        path.lineTo(8, 18)
        path.closeSubpath()
        painter.drawPath(path)
    elif icon_type == "pause":
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(7, 6, 3, 12, 1, 1)
        painter.drawRoundedRect(14, 6, 3, 12, 1, 1)
    elif icon_type == "resume":
        path = QPainterPath()
        path.arcMoveTo(QRectF(5, 5, 14, 14), 45)
        path.arcTo(QRectF(5, 5, 14, 14), 45, 270)
        painter.drawPath(path)
        painter.setBrush(QColor(color))
        arrow = QPainterPath()
        arrow.moveTo(14, 4)
        arrow.lineTo(19, 7)
        arrow.lineTo(14, 10)
        arrow.closeSubpath()
        painter.drawPath(arrow)
    elif icon_type == "delete":
        painter.drawLine(6, 6, 18, 18)
        painter.drawLine(18, 6, 6, 18)
    elif icon_type == "info":
        painter.drawEllipse(4, 4, 16, 16)
        painter.drawLine(12, 10, 12, 16)
        painter.drawPoint(12, 7)
    elif icon_type == "folder":
        path = QPainterPath()
        path.moveTo(4, 7)
        path.lineTo(9, 7)
        path.lineTo(11, 9)
        path.lineTo(20, 9)
        path.lineTo(20, 18)
        path.lineTo(4, 18)
        path.closeSubpath()
        painter.drawPath(path)
    elif icon_type == "app":
        painter.setBrush(QColor("#3b82f6"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(3, 3, 18, 18, 4, 4)
        pen.setColor(QColor("#ffffff"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(12, 7, 12, 15)
        painter.drawLine(8, 11, 12, 15)
        painter.drawLine(16, 11, 12, 15)
    painter.end()
    return QIcon(pix)


# -----------------------------------------------------------------------------
# Modern Table Progress Delegate
# -----------------------------------------------------------------------------
class ModernProgressDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        job = index.data(Qt.UserRole)
        if job is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect.adjusted(6, option.rect.height() // 2 - 8,
                                    -6, -(option.rect.height() // 2 - 8))

        # Background track
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1e293b"))
        painter.drawRoundedRect(rect, 5, 5)

        w = rect.width()
        total = job.total

        if total > 0:
            if job.parts:
                for part in job.parts:
                    p_start = part.get("start", 0)
                    p_have = part.get("have", 0)
                    x0 = rect.left() + (p_start * w // total)
                    x1 = rect.left() + ((p_start + p_have) * w // total)
                    seg_w = max(0, x1 - x0)
                    if seg_w > 0:
                        color = QColor("#3b82f6") if job.state == "downloading" else (
                            QColor("#10b981") if job.state == "completed" else QColor("#f59e0b")
                        )
                        painter.setBrush(color)
                        painter.drawRoundedRect(int(x0), rect.top() + 1, int(seg_w), rect.height() - 2, 3, 3)
            else:
                pct = job.done / total
                fill_w = int(w * pct)
                if fill_w > 0:
                    color = QColor("#3b82f6") if job.state == "downloading" else (
                        QColor("#10b981") if job.state == "completed" else QColor("#f59e0b")
                    )
                    painter.setBrush(color)
                    painter.drawRoundedRect(rect.left(), rect.top() + 1, fill_w, rect.height() - 2, 3, 3)

        # Percentage Text inside/over bar
        painter.setPen(QColor("#f8fafc"))
        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        pct_text = f"{int(job.done * 100 // total)}%" if total else "0%"
        painter.drawText(option.rect.adjusted(0, 0, -12, 0), Qt.AlignRight | Qt.AlignVCenter, pct_text)

        painter.restore()


# -----------------------------------------------------------------------------
# Dialogs: Add URL, Details, Download Prompt
# -----------------------------------------------------------------------------
class AddUrlDialog(QDialog):
    def __init__(self, default_dir: str, parent=None, default_url="", suggested_name=""):
        super().__init__(parent)
        self.setWindowTitle("New Download — OpenDM")
        self.setMinimumWidth(560)
        self.dest_dir = default_dir

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel("Enter Download Details")
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #f8fafc;")
        layout.addWidget(header)

        self.url = QLineEdit(default_url)
        self.url.setPlaceholderText("https://example.com/software-release.zip")
        self.url.setClearButtonEnabled(True)
        layout.addWidget(self.url)

        # Filename override
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("File Name:"))
        self.fn_input = QLineEdit(suggested_name)
        self.fn_input.setPlaceholderText("(auto-detected if blank)")
        name_row.addWidget(self.fn_input)
        layout.addLayout(name_row)

        # Settings grid
        row = QHBoxLayout()
        row.setSpacing(12)

        row.addWidget(QLabel("Threads / Connections:"))
        self.conns = QSpinBox()
        self.conns.setRange(1, 32)
        self.conns.setValue(4)
        self.conns.setFixedWidth(80)
        row.addWidget(self.conns)

        row.addSpacing(20)
        row.addWidget(QLabel("Save Folder:"))
        self.folder_label = QLabel(self.dest_dir)
        self.folder_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        row.addWidget(self.folder_label, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        row.addWidget(browse_btn)

        layout.addLayout(row)

        # Pre-flight metadata label (Size & Range support)
        self.meta_lbl = QLabel("")
        self.meta_lbl.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 500;")
        layout.addWidget(self.meta_lbl)

        layout.addSpacing(8)

        # Action Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        start_btn = QPushButton("Start Download")
        start_btn.setDefault(True)
        start_btn.setStyleSheet("""
            QPushButton {
                background: #2563eb; color: white; font-weight: bold;
                border: none; padding: 8px 20px; border-radius: 6px;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        start_btn.clicked.connect(self.accept)
        btn_box.addWidget(start_btn)

        layout.addLayout(btn_box)

        # Auto-paste from clipboard if URL
        cb = QApplication.clipboard().text().strip()
        if cb.startswith(("http://", "https://")) and not default_url:
            self.url.setText(cb)

        # Trigger async pre-flight metadata resolution
        self.url.textChanged.connect(self._trigger_preflight)
        if self.url.text().strip():
            self._trigger_preflight()

    def _trigger_preflight(self):
        u = self.url.text().strip()
        if not u.startswith(("http://", "https://")):
            return
        self.meta_lbl.setText("Resolving file metadata…")
        threading.Thread(target=self._run_preflight, args=(u,), daemon=True).start()

    def _run_preflight(self, u: str):
        try:
            from . import engine
            meta = engine.probe(u)
            size = meta.get("size", 0)
            ranges = meta.get("ranges", False)
            cd_name = meta.get("content_disposition_filename")

            mb = size / (1024 * 1024) if size else 0
            size_str = f"{mb:.2f} MB ({size:,} bytes)" if size else "Unknown size"
            range_str = "Accelerated (Range supported)" if ranges else "Single-stream (No Range)"

            def update_ui():
                self.meta_lbl.setText(f"Size: {size_str}  •  Protocol: {range_str}")
                if cd_name and not self.fn_input.text().strip():
                    self.fn_input.setText(cd_name)

            QTimer.singleShot(0, update_ui)
        except Exception:
            pass

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Download Folder", self.dest_dir)
        if d:
            self.dest_dir = d
            self.folder_label.setText(d)


class DetailsDialog(QDialog):
    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Download Progress — {job.filename}")
        self.resize(680, 420)
        self.job = job

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title_box = QFrame()
        title_box.setStyleSheet("background: #0f172a; border-radius: 8px; padding: 12px;")
        t_lay = QVBoxLayout(title_box)
        t_lay.setSpacing(4)

        self.fn_lbl = QLabel(f"<b>File:</b> {job.filename}")
        self.fn_lbl.setStyleSheet("font-size: 14px; color: #f8fafc;")
        self.url_lbl = QLabel(f"<b>URL:</b> {job.url}")
        self.url_lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.url_lbl.setWordWrap(True)

        t_lay.addWidget(self.fn_lbl)
        t_lay.addWidget(self.url_lbl)
        layout.addWidget(title_box)

        m_lay = QHBoxLayout()
        self.stat_lbl = QLabel(f"State: {job.state}")
        self.size_lbl = QLabel("Size: 0 MB")
        self.speed_lbl = QLabel("Speed: 0 MB/s")
        for lbl in (self.stat_lbl, self.size_lbl, self.speed_lbl):
            lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #cbd5e1;")
            m_lay.addWidget(lbl)
        layout.addLayout(m_lay)

        seg_head = QLabel("Per-Connection Transfer Slices:")
        seg_head.setStyleSheet("font-size: 12px; font-weight: bold; color: #94a3b8;")
        layout.addWidget(seg_head)

        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setStyleSheet("""
            QPlainTextEdit {
                background: #0f172a; color: #38bdf8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px; border: 1px solid #1e293b; border-radius: 6px;
            }
        """)
        layout.addWidget(self.info)

        b_row = QHBoxLayout()
        b_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        b_row.addWidget(close_btn)
        layout.addLayout(b_row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)
        self.refresh()

    def refresh(self):
        j = self.job
        mb = 1024 * 1024
        self.stat_lbl.setText(f"State: {j.state.upper()}")
        done_mb = j.done / mb
        tot_mb = j.total / mb if j.total else 0
        pct = (j.done * 100 // j.total) if j.total else 0
        self.size_lbl.setText(f"Progress: {done_mb:.2f} / {tot_mb:.2f} MB ({pct}%)")
        self.speed_lbl.setText(f"Speed: {j.speed_avg / mb:.2f} MB/s [{j.conns} conns]")

        lines = []
        for i, p in enumerate(j.parts):
            got = p.get("have", 0)
            span = p.get("end", 0) - p.get("start", 0) + 1
            ratio = min(max(got / max(span, 1), 0.0), 1.0)
            bar_len = 28
            filled = int(ratio * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            pct_val = int(ratio * 100)
            is_stolen = " [Work-Stolen]" if i >= j.conns else ""
            lines.append(f"Segment #{i+1:02d}{is_stolen} [{p.get('start',0):>10,} .. {p.get('end',0):>10,}] : {bar} {pct_val:>3}% ({got/mb:.2f} MB)")

        if j.sha256:
            lines.append("")
            lines.append(f"SHA-256 Verified: {j.sha256}")
        if j.error:
            lines.append("")
            lines.append(f"Error Diagnostic: {j.error}")

        self.info.setPlainText("\n".join(lines))


# -----------------------------------------------------------------------------
# Intercept Bridge for GUI Prompt Threading
# -----------------------------------------------------------------------------
class PromptBridge(QObject):
    sig_prompt = Signal(str, int, dict, object)


# -----------------------------------------------------------------------------
# Main Application Window (Fluent / Clean Modern Layout)
# -----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Advanced Download Manager")
        self.resize(1100, 680)
        self.setWindowIcon(make_icon("app", "#3b82f6"))

        dest = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(dest, exist_ok=True)
        self.qm = QueueManager(dest)

        # Connect GUI hook for download requests intercepted by daemon/extension
        self.prompt_bridge = PromptBridge()
        self.prompt_bridge.sig_prompt.connect(self._handle_prompt_in_main_thread)
        self.qm.on_new_download_request = lambda u, c, h, s: self.prompt_bridge.sig_prompt.emit(u, c, h, s)

        self._daemon_thread = None
        self._start_embedded_daemon()

        self.selected_category = "All"
        self._items_by_id = {}

        self._build_ui()
        self._setup_system_tray()

        # Update timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_rows)
        self.timer.start(350)

    def _start_embedded_daemon(self):
        try:
            from . import daemon as daemon_mod
            daemon_mod.QM = self.qm
            httpd = HTTPServer(("127.0.0.1", daemon_mod.HTTP_PORT), daemon_mod.HardenedApiHandler)
            self._daemon_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._daemon_thread.start()
        except Exception:
            pass

    def _setup_system_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon("app", "#3b82f6"), self)
        tray_menu = QMenu()

        act_show = tray_menu.addAction("Show OpenDM")
        act_show.triggered.connect(self.showNormal)

        act_add = tray_menu.addAction("Add New Download…")
        act_add.triggered.connect(self.add_url)

        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("Exit")
        act_quit.triggered.connect(self._force_quit)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.activateWindow()

    def closeEvent(self, event):
        """Minimize to system tray on close instead of killing active downloads."""
        if hasattr(self, "tray") and self.tray.isVisible():
            self.hide()
            self.tray.showMessage(
                "OpenDM Running in Background",
                "Downloads and browser integration remain active in the system tray.",
                QSystemTrayIcon.Information,
                2000
            )
            event.ignore()
        else:
            event.accept()

    def _force_quit(self):
        QApplication.quit()

    def _handle_prompt_in_main_thread(self, url, conns, headers, suggested):
        """Opens a confirmation dialog on screen upon interception."""
        self.showNormal()
        self.activateWindow()
        dlg = AddUrlDialog(self.qm.dest_dir, self, default_url=url, suggested_name=suggested or "")
        dlg.conns.setValue(conns)
        if dlg.exec():
            final_name = dlg.fn_input.text().strip() or suggested
            job = self.qm.add(
                dlg.url.text().strip(),
                conns=dlg.conns.value(),
                dest_dir=dlg.dest_dir,
                headers=headers,
                suggested_filename=final_name
            )
            job.start()
            self.refresh_rows()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # 1. Header Toolbar
        toolbar = QWidget()
        toolbar.setObjectName("TopToolbar")
        t_lay = QHBoxLayout(toolbar)
        t_lay.setContentsMargins(14, 10, 14, 10)
        t_lay.setSpacing(8)

        # App Brand
        brand = QLabel(" OpenDM")
        brand.setStyleSheet("font-size: 16px; font-weight: 800; color: #38bdf8; margin-right: 12px;")
        t_lay.addWidget(brand)

        # Actions
        self.btn_add = QPushButton(" Add URL")
        self.btn_add.setIcon(make_icon("add", "#ffffff"))
        self.btn_add.setStyleSheet("""
            QPushButton {
                background: #2563eb; color: white; font-weight: bold;
                border: none; padding: 7px 16px; border-radius: 6px;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        self.btn_add.clicked.connect(self.add_url)
        t_lay.addWidget(self.btn_add)

        self.btn_start = QPushButton(" Resume")
        self.btn_start.setIcon(make_icon("play", "#22c55e"))
        self.btn_start.clicked.connect(self.resume_selected)
        t_lay.addWidget(self.btn_start)

        self.btn_pause = QPushButton(" Pause")
        self.btn_pause.setIcon(make_icon("pause", "#f59e0b"))
        self.btn_pause.clicked.connect(self.pause_selected)
        t_lay.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton(" Cancel")
        self.btn_cancel.setIcon(make_icon("delete", "#ef4444"))
        self.btn_cancel.clicked.connect(self.cancel_selected)
        t_lay.addWidget(self.btn_cancel)

        self.btn_details = QPushButton(" Details")
        self.btn_details.setIcon(make_icon("info", "#94a3b8"))
        self.btn_details.clicked.connect(self.details_selected)
        t_lay.addWidget(self.btn_details)

        self.btn_open_folder = QPushButton(" Open Folder")
        self.btn_open_folder.setIcon(make_icon("folder", "#94a3b8"))
        self.btn_open_folder.clicked.connect(self.open_downloads_folder)
        t_lay.addWidget(self.btn_open_folder)

        self.btn_settings = QPushButton(" Settings")
        self.btn_settings.setIcon(make_icon("settings", "#94a3b8"))
        self.btn_settings.clicked.connect(self.open_settings)
        t_lay.addWidget(self.btn_settings)

        t_lay.addSpacing(16)
        t_lay.addWidget(QLabel("Speed Limit:"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 100)  # 0 = Unlimited, 100 = 100 MB/s
        self.speed_slider.setValue(0)
        self.speed_slider.setFixedWidth(120)
        self.speed_slider_lbl = QLabel("Unlimited")
        self.speed_slider_lbl.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold; min-width: 65px;")
        self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        t_lay.addWidget(self.speed_slider)
        t_lay.addWidget(self.speed_slider_lbl)

        t_lay.addStretch()
        root_lay.addWidget(toolbar)

        # 2. Main Content Splitter (Sidebar + Downloads List)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        # Left Sidebar (Categories)
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        s_lay = QVBoxLayout(sidebar)
        s_lay.setContentsMargins(12, 16, 12, 16)
        s_lay.setSpacing(4)

        cat_title = QLabel("CATEGORIES")
        cat_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #64748b; margin-bottom: 8px;")
        s_lay.addWidget(cat_title)

        self.cat_buttons = {}
        cats = [
            ("All", "All Downloads"),
            ("Downloading", "Downloading"),
            ("Completed", "Completed"),
            ("Paused", "Paused"),
            ("Compressed", "Compressed (Zip/Rar)"),
            ("Video", "Video & Media"),
            ("Documents", "Documents"),
            ("Programs", "Applications"),
        ]
        for key, title in cats:
            btn = QPushButton(f"  {title}")
            btn.setObjectName("CategoryBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda ch, k=key: self.set_category(k))
            s_lay.addWidget(btn)
            self.cat_buttons[key] = btn

        self.cat_buttons["All"].setChecked(True)
        s_lay.addStretch()
        splitter.addWidget(sidebar)

        # Right Area (Table)
        right_container = QWidget()
        r_lay = QVBoxLayout(right_container)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File Name", "Size", "Progress", "Speed", "Status", "Category", "Verification"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 95)
        self.tree.setColumnWidth(2, 220)
        self.tree.setColumnWidth(3, 110)
        self.tree.setColumnWidth(4, 105)
        self.tree.setColumnWidth(5, 95)
        self.tree.setColumnWidth(6, 140)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setItemDelegateForColumn(2, ModernProgressDelegate(self.tree))
        self.tree.itemDoubleClicked.connect(self.details_item)
        self.tree.itemSelectionChanged.connect(self._update_actions_state)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        r_lay.addWidget(self.tree)
        splitter.addWidget(right_container)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root_lay.addWidget(splitter, 1)

        # 3. Modern Status Bar
        self.status_bar = QStatusBar()
        self.stat_label = QLabel("Ready • Engine Online")
        self.stat_label.setStyleSheet("color: #94a3b8; font-size: 12px; margin-left: 8px;")
        self.status_bar.addWidget(self.stat_label)
        self.setStatusBar(self.status_bar)

    def set_category(self, key: str):
        self.selected_category = key
        for k, btn in self.cat_buttons.items():
            btn.setChecked(k == key)
        self.refresh_rows(force_refilter=True)

    def _show_context_menu(self, pos):
        it = self.tree.itemAt(pos)
        if not it:
            return
        job = it.data(0, Qt.UserRole)
        if not job:
            return

        menu = QMenu(self)
        act_details = menu.addAction("Inspect Details…")
        act_details.triggered.connect(lambda: DetailsDialog(job, self).exec())

        menu.addSeparator()
        if job.state == "downloading":
            act_pause = menu.addAction("Pause Download")
            act_pause.triggered.connect(job.pause)
        elif job.state in ("paused", "error", "queued"):
            act_resume = menu.addAction("Resume Download")
            act_resume.triggered.connect(job.resume)

        act_cancel = menu.addAction("Cancel / Remove")
        act_cancel.triggered.connect(job.cancel)

        if job.state in ("paused", "error"):
            act_refresh = menu.addAction("Refresh Download Address…")
            act_refresh.triggered.connect(lambda: self._refresh_job_address(job))

        if job.state == "completed" and os.path.exists(job.dest):
            menu.addSeparator()
            act_open = menu.addAction("Open Downloaded File")
            act_open.triggered.connect(lambda: self._open_file(job.dest))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _open_file(self, path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _refresh_job_address(self, job):
        """Allows updating URL, Cookies, and Referer for an existing partially downloaded job."""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Refresh Address — {job.filename}")
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"Update expired/invalid download address for <b>{job.filename}</b>:"))

        url_in = QLineEdit(job.url)
        url_in.setClearButtonEnabled(True)
        lay.addWidget(url_in)

        # Pre-fill clipboard if URL
        cb = QApplication.clipboard().text().strip()
        if cb.startswith(("http://", "https://")) and cb != job.url:
            url_in.setText(cb)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec():
            new_u = url_in.text().strip()
            if new_u.startswith(("http://", "https://")):
                job.url = new_u
                if job.state == "error":
                    job.state = "paused"
                self.qm._save()
                self.refresh_rows()
                self._update_actions_state()
                QMessageBox.information(
                    self, "Address Updated",
                    "Download URL has been refreshed successfully.\nYou can now Resume the download."
                )

    def _on_speed_slider_changed(self, val: int):
        if val == 0:
            self.speed_slider_lbl.setText("Unlimited")
            limit_bytes = 0
        else:
            self.speed_slider_lbl.setText(f"{val} MB/s")
            limit_bytes = val * 1024 * 1024
        for job in self.qm.jobs:
            if hasattr(job, "speed_limit"):
                job.speed_limit = limit_bytes

    def open_settings(self):
        """Proxy configuration dialog (HTTP / SOCKS5)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Network & Proxy Settings")
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)

        info = QLabel("Configure network proxy for all download requests (e.g., SOCKS5 or HTTP):")
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QFormLayout()
        proxy_in = QLineEdit()
        proxy_in.setPlaceholderText("socks5://127.0.0.1:10808 or http://127.0.0.1:8080")
        current_proxy = getattr(self.qm, "proxy", "") or ""
        proxy_in.setText(current_proxy)
        form.addRow("Proxy URL:", proxy_in)
        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec():
            p_val = proxy_in.text().strip()
            self.qm.proxy = p_val if p_val else None
            QMessageBox.information(self, "Settings Saved", f"Proxy set to: {p_val or 'None (Direct connection)'}")

    def open_downloads_folder(self):
        d = self.qm.dest_dir
        self._open_file(d)

    def add_url(self):
        dlg = AddUrlDialog(self.qm.dest_dir, self)
        if dlg.exec() and dlg.url.text().strip():
            final_name = dlg.fn_input.text().strip() or None
            job = self.qm.add(
                dlg.url.text().strip(),
                conns=dlg.conns.value(),
                dest_dir=dlg.dest_dir,
                suggested_filename=final_name
            )
            job.start()
            self.refresh_rows()

    def _selected_job(self):
        items = self.tree.selectedItems()
        it = items[0] if items else self.tree.currentItem()
        if not it:
            return None
        return it.data(0, Qt.UserRole) or it.data(2, Qt.UserRole)

    def _update_actions_state(self):
        j = self._selected_job()
        has_sel = j is not None
        self.btn_details.setEnabled(has_sel)
        self.btn_cancel.setEnabled(has_sel)
        if has_sel:
            self.btn_pause.setEnabled(j.state == "downloading")
            self.btn_start.setEnabled(j.state in ("paused", "error", "queued"))
        else:
            self.btn_pause.setEnabled(True)
            self.btn_start.setEnabled(True)

    def pause_selected(self):
        j = self._selected_job()
        if j:
            j.pause()
            self.refresh_rows()
            self._update_actions_state()

    def resume_selected(self):
        j = self._selected_job()
        if j and j.state in ("paused", "error", "queued"):
            j.resume()
            self.refresh_rows()
            self._update_actions_state()

    def cancel_selected(self):
        j = self._selected_job()
        if j:
            j.cancel()
            self.refresh_rows()
            self._update_actions_state()

    def details_selected(self):
        j = self._selected_job()
        if j:
            DetailsDialog(j, self).exec()

    def details_item(self, item, _col):
        j = item.data(0, Qt.UserRole) or item.data(2, Qt.UserRole)
        if j:
            DetailsDialog(j, self).exec()

    def refresh_rows(self, force_refilter=False):
        jobs = list(self.qm.jobs)
        mb = 1024 * 1024

        current_job = self._selected_job()
        selected_id = current_job.id if current_job else None

        cat = self.selected_category
        filtered_jobs = []
        for j in jobs:
            if cat == "All":
                filtered_jobs.append(j)
            elif cat in ("Downloading", "Completed", "Paused"):
                if j.state.lower() == cat.lower():
                    filtered_jobs.append(j)
            elif j.category == cat:
                filtered_jobs.append(j)

        seen_ids = set()
        for j in filtered_jobs:
            seen_ids.add(j.id)
            it = self._items_by_id.get(j.id)
            status_text = j.state.capitalize()

            if not it:
                it = QTreeWidgetItem([
                    j.filename,
                    f"{j.total / mb:.1f} MB" if j.total else "—",
                    "",
                    f"{j.speed_avg / mb:.2f} MB/s" if j.state == "downloading" else "—",
                    status_text,
                    j.category,
                    j.sha256[:10] + "…" if j.sha256 else "—",
                ])
                it.setData(0, Qt.UserRole, j)
                it.setData(2, Qt.UserRole, j)
                self.tree.addTopLevelItem(it)
                self._items_by_id[j.id] = it
            else:
                it.setText(0, j.filename)
                it.setText(1, f"{j.total / mb:.1f} MB" if j.total else "—")
                it.setText(3, f"{j.speed_avg / mb:.2f} MB/s" if j.state == "downloading" else "—")
                it.setText(4, status_text)
                it.setText(5, j.category)
                it.setText(6, j.sha256[:10] + "…" if j.sha256 else "—")
                it.setData(0, Qt.UserRole, j)
                it.setData(2, Qt.UserRole, j)

        for jid in list(self._items_by_id.keys()):
            if jid not in seen_ids:
                it = self._items_by_id.pop(jid)
                idx = self.tree.indexOfTopLevelItem(it)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)

        if selected_id and selected_id in self._items_by_id:
            self.tree.setCurrentItem(self._items_by_id[selected_id])

        active = sum(1 for j in jobs if j.state == "downloading")
        done = sum(1 for j in jobs if j.state == "completed")
        self.stat_label.setText(f"Total: {len(jobs)}  •  Active: {active}  •  Completed: {done}  •  Directory: {self.qm.dest_dir}")


# -----------------------------------------------------------------------------
# Premium Dark Theme Stylesheet (Modern Fluent Slate)
# -----------------------------------------------------------------------------
MODERN_THEME = """
QMainWindow {
    background-color: #0b0f19;
}
QWidget {
    color: #e2e8f0;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    font-size: 13px;
}
#TopToolbar {
    background-color: #0f172a;
    border-bottom: 1px solid #1e293b;
}
QPushButton {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #334155;
    border-color: #475569;
}
QPushButton:pressed {
    background-color: #0f172a;
}
#Sidebar {
    background-color: #0f172a;
    border-right: 1px solid #1e293b;
}
#CategoryBtn {
    background-color: transparent;
    border: none;
    border-radius: 6px;
    text-align: left;
    padding: 8px 12px;
    color: #94a3b8;
    font-weight: 500;
}
#CategoryBtn:hover {
    background-color: #1e293b;
    color: #f8fafc;
}
#CategoryBtn:checked {
    background-color: #1e3a8a;
    color: #60a5fa;
    font-weight: 600;
}
QTreeWidget {
    background-color: #0b0f19;
    alternate-background-color: #0f172a;
    border: none;
    selection-background-color: #1e293b;
    selection-color: #38bdf8;
    padding: 4px;
}
QTreeWidget::item {
    height: 36px;
    border-radius: 4px;
    padding: 2px;
}
QTreeWidget::item:hover {
    background-color: #1e293b;
}
QTreeWidget::item:selected {
    background-color: #1e293b;
    color: #38bdf8;
}
QHeaderView::section {
    background-color: #0f172a;
    color: #94a3b8;
    border: none;
    border-bottom: 1px solid #1e293b;
    padding: 8px 10px;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
}
QStatusBar {
    background-color: #0f172a;
    border-top: 1px solid #1e293b;
    color: #94a3b8;
}
QMenu {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 24px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #2563eb;
    color: white;
}
QDialog {
    background-color: #0f172a;
}
QLineEdit, QSpinBox {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #f8fafc;
    padding: 6px 10px;
}
QLineEdit:focus, QSpinBox:focus {
    border: 1px solid #3b82f6;
}
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(MODERN_THEME)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
