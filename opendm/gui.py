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
import shutil

from PySide6.QtCore import Qt, QTimer, QSize, QRectF, Signal, QObject, QPoint
from PySide6.QtGui import (
    QAction, QColor, QIcon, QPainter, QFont, QFontMetrics, QPen, QBrush,
    QKeySequence, QPixmap, QPainterPath
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton, QLabel, QMenu, QMenuBar,
    QLineEdit, QComboBox, QSpinBox, QDialog, QToolBar, QStatusBar,
    QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QMessageBox,
    QFileDialog, QDialogButtonBox, QPlainTextEdit, QFrame, QSplitter,
    QSystemTrayIcon, QSlider, QCheckBox, QFormLayout, QToolButton,
    QScrollArea, QGridLayout
)

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
    elif icon_type == "check":
        painter.drawLine(7, 13, 11, 17)
        painter.drawLine(11, 17, 18, 7)
    elif icon_type == "clock":
        painter.drawEllipse(5, 5, 14, 14)
        painter.drawLine(12, 8, 12, 12)
        painter.drawLine(12, 12, 15, 14)
    elif icon_type == "gear":
        painter.drawEllipse(9, 9, 6, 6)
        for _cx, _cy, _dx, _dy in (
            (12, 3, 12, 6), (12, 18, 12, 21), (3, 12, 6, 12), (18, 12, 21, 12),
            (6, 6, 8, 8), (16, 16, 18, 18), (18, 6, 16, 8), (8, 16, 6, 18),
        ):
            painter.drawLine(_cx, _cy, _dx, _dy)
    elif icon_type == "search":
        painter.drawEllipse(5, 5, 12, 12)
        painter.drawLine(14, 14, 20, 20)
    elif icon_type == "gauge":
        _path = QPainterPath()
        _path.arcMoveTo(QRectF(4, 6, 16, 16), 180)
        _path.arcTo(QRectF(4, 6, 16, 16), 180, 180)
        painter.drawPath(_path)
        painter.drawLine(12, 14, 17, 9)
        painter.drawPoint(12, 14)
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
# Update-flow helpers (spec item 15)
#
# The SHA-256 shown before the update download is only the *expected* value
# published with the release. It must never be presented as "Verified" --
# verification happens after the download completes, in
# MainWindow._finish_update_download via updater.verify_installer.
# -----------------------------------------------------------------------------
def update_sha_offer_line(expected_sha256: str | None) -> str:
    """Text for the update-offer dialog shown BEFORE any download.

    Deliberately says "Expected", never "Verified": nothing has been
    downloaded or compared yet at this point.
    """
    if expected_sha256:
        return f"Expected SHA-256: {expected_sha256[:16]}…"
    return "No SHA-256 checksum published for this release — automatic install is unavailable."


def update_no_hash_notice_line(html_url: str | None) -> str:
    """Notice shown instead of the auto-install button when the release
    publishes no SHA-256 checksum. Points the user at the release page
    for a manual review/download."""
    url = html_url or "(no release URL provided)"
    return (
        "No SHA-256 checksum was published for this release, so automatic "
        "install is disabled. Review the release and download manually:\n"
        f"{url}"
    )


# -----------------------------------------------------------------------------
# Card-layout redesign — metadata, helpers and widgets
# -----------------------------------------------------------------------------

STATUS_META = {
    "downloading": ("Downloading", "#3b82f6"),
    "paused":      ("Paused",      "#f59e0b"),
    "completed":   ("Completed",   "#22c55e"),
    "error":       ("Error",       "#ef4444"),
    "canceled":    ("Canceled",    "#64748b"),
    "queued":      ("Queued",      "#94a3b8"),
}

# category key -> (display label, badge color)
CATEGORY_META = {
    "Compressed": ("Archives",  "#f59e0b"),
    "Video":      ("Media",     "#a78bfa"),
    "Music":      ("Music",     "#38bdf8"),
    "Documents":  ("Documents", "#34d399"),
    "Programs":   ("Programs",  "#f472b6"),
    "General":    ("Other",     "#64748b"),
}

LIBRARY_ITEMS = [
    ("All",       "All Downloads", "app",   "#38bdf8"),
    ("Active",    "Active",        "play",  "#22c55e"),
    ("Completed", "Completed",     "check", "#22c55e"),
    ("Queued",    "Queued",        "clock", "#94a3b8"),
]


def _fmt_size(num):
    """Human-readable byte size (MB/GB), '—' when unknown."""
    if not num:
        return "—"
    gb = 1024 ** 3
    mb = 1024 ** 2
    if num >= gb:
        return f"{num / gb:.1f} GB"
    return f"{num / mb:.1f} MB"


def _fmt_eta(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s left"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s left"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m left"


def _with_alpha(hex_color, alpha_hex):
    """Apply alpha to a #RRGGBB color for Qt stylesheets (which use #AARRGGBB)."""
    return f"#{alpha_hex}{hex_color[1:]}"


class ClickableFrame(QFrame):
    """QFrame that emits clicked on left mouse press (for pill-style buttons)."""

    clicked = Signal()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class SegmentBar(QWidget):
    """Thin per-connection segment progress bar — OpenDM's signature visual."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self._parts = []
        self._total = 0
        self._done = 0
        self._color = "#3b82f6"

    def set_job(self, job):
        self._parts = list(getattr(job, "parts", None) or [])
        self._total = getattr(job, "total", 0) or 0
        self._done = getattr(job, "done", 0) or 0
        state = (getattr(job, "state", "") or "").lower()
        self._color = STATUS_META.get(state, ("", "#3b82f6"))[1]
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1e293b"))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)
        if self._total > 0:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)
            p.setClipPath(path)
            color = QColor(self._color)
            if self._parts:
                x = 0.0
                for part in self._parts:
                    s = part.get("start", 0)
                    e = part.get("end", 0)
                    seg = max(e - s + 1, 1)
                    have = max(0, min(part.get("have", 0), seg))
                    seg_w = w * seg / self._total
                    if have > 0:
                        p.setBrush(color)
                        p.drawRect(QRectF(x, 0.0, seg_w * have / seg, h))
                    x += seg_w
                    p.setBrush(QColor("#0b0f19"))
                    p.drawRect(QRectF(x, 0.0, 1.0, h))
                    x += 1.0
            else:
                frac = max(0.0, min(1.0, self._done / self._total))
                if frac > 0:
                    p.setBrush(color)
                    p.drawRect(QRectF(0.0, 0.0, w * frac, h))
        p.end()


class StatusPill(QLabel):
    """Small colored status badge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_status("queued")

    def set_status(self, state):
        label, color = STATUS_META.get(state, (state.capitalize(), "#94a3b8"))
        self.setText(label)
        self.setStyleSheet(
            f"background: {_with_alpha(color, '26')}; color: {color};"
            f" border: 1px solid {_with_alpha(color, '66')};"
            " border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600;"
        )


class FileBadge(QLabel):
    """Rounded file-type badge showing the extension."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setAlignment(Qt.AlignCenter)

    def set_file(self, filename, category):
        ext = filename.rsplit(".", 1)[-1].upper()[:4] if "." in filename else "FILE"
        color = CATEGORY_META.get(category, ("", "#64748b"))[1]
        self.setText(ext)
        self.setStyleSheet(
            f"background: {_with_alpha(color, '26')}; color: {color};"
            " border-radius: 10px; font-size: 11px; font-weight: 800;"
        )


class NavItem(QWidget):
    """Sidebar navigation row with icon/dot, label and count badge."""

    clicked = Signal(str)

    def __init__(self, key, label, icon=None, color="#94a3b8", show_badge=True, parent=None):
        super().__init__(parent)
        self.key = key
        self._show_badge = show_badge
        self.setObjectName("NavItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        if icon:
            il = QLabel()
            il.setPixmap(make_icon(icon, color, 16).pixmap(16, 16))
            lay.addWidget(il)
        else:
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
            dot.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(dot)
        text = QLabel(label)
        text.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(text, 1)
        self.badge = QLabel("0")
        self.badge.setObjectName("NavBadge")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.setVisible(False)
        lay.addWidget(self.badge)

    def set_count(self, n):
        self.badge.setText(str(n))
        self.badge.setVisible(self._show_badge and n > 0)

    def set_selected(self, sel):
        if self.property("selected") != sel:
            self.setProperty("selected", sel)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(ev)


class DownloadCard(QFrame):
    """Rich download card: badge, name, meta, segment bar, status, actions."""

    clicked = Signal(object)
    doubleClicked = Signal(object)
    contextRequested = Signal(object, object)
    actionRequested = Signal(object, str)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.badge = FileBadge()
        top.addWidget(self.badge)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        self.name_lbl = QLabel()
        self.name_lbl.setObjectName("CardName")
        self.meta_lbl = QLabel()
        self.meta_lbl.setObjectName("CardMeta")
        mid.addWidget(self.name_lbl)
        mid.addWidget(self.meta_lbl)
        top.addLayout(mid, 1)

        self.pill = StatusPill()
        top.addWidget(self.pill, 0, Qt.AlignTop)
        lay.addLayout(top)

        self.bar = SegmentBar()
        lay.addWidget(self.bar)

        foot = QHBoxLayout()
        foot.setSpacing(2)
        self.info_lbl = QLabel()
        self.info_lbl.setObjectName("CardInfo")
        foot.addWidget(self.info_lbl)
        foot.addStretch(1)

        self.act_toggle = QToolButton()
        self.act_toggle.setObjectName("CardAction")
        self.act_toggle.setCursor(Qt.PointingHandCursor)
        self.act_toggle.clicked.connect(lambda: self.actionRequested.emit(self.job, "toggle"))
        foot.addWidget(self.act_toggle)

        self.act_folder = QToolButton()
        self.act_folder.setObjectName("CardAction")
        self.act_folder.setCursor(Qt.PointingHandCursor)
        self.act_folder.setIcon(make_icon("folder", "#94a3b8", 16))
        self.act_folder.setToolTip("Open containing folder")
        self.act_folder.clicked.connect(lambda: self.actionRequested.emit(self.job, "folder"))
        foot.addWidget(self.act_folder)

        self.act_cancel = QToolButton()
        self.act_cancel.setObjectName("CardAction")
        self.act_cancel.setCursor(Qt.PointingHandCursor)
        self.act_cancel.setIcon(make_icon("delete", "#94a3b8", 16))
        self.act_cancel.setToolTip("Cancel / remove")
        self.act_cancel.clicked.connect(lambda: self.actionRequested.emit(self.job, "cancel"))
        foot.addWidget(self.act_cancel)

        lay.addLayout(foot)
        self.refresh(job)

    def set_selected(self, sel):
        if self.property("selected") != sel:
            self.setProperty("selected", sel)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def refresh(self, job):
        self.job = job
        fm = QFontMetrics(self.name_lbl.font())
        self.name_lbl.setText(fm.elidedText(job.filename, Qt.ElideRight, 250))
        self.name_lbl.setToolTip(job.filename)
        state = (job.state or "").lower()
        self.badge.set_file(job.filename, job.category)
        self.pill.set_status(state)
        self.bar.set_job(job)

        total = job.total or 0
        done = min(job.done or 0, total) if total else 0
        pct = int(round(100.0 * done / total)) if total else 0
        size_s = _fmt_size(total)
        toggle_enabled = True

        if state == "downloading":
            speed = job.speed_avg or 0
            self.meta_lbl.setText(f"{size_s}  •  {_fmt_size(speed)}/s" if speed else size_s)
            eta = _fmt_eta((total - done) / speed) if speed and total else ""
            self.info_lbl.setText(f"{pct}%" + (f"  •  {eta}" if eta else ""))
            self.act_toggle.setIcon(make_icon("pause", "#f59e0b", 16))
            self.act_toggle.setToolTip("Pause")
        elif state == "paused":
            self.meta_lbl.setText(size_s)
            self.info_lbl.setText(f"{pct}%  •  Paused")
            self.act_toggle.setIcon(make_icon("play", "#22c55e", 16))
            self.act_toggle.setToolTip("Resume")
        elif state == "error":
            self.meta_lbl.setText(size_s)
            self.info_lbl.setText(f"{pct}%  •  Error — right-click for options")
            self.act_toggle.setIcon(make_icon("play", "#22c55e", 16))
            self.act_toggle.setToolTip("Retry")
        elif state == "queued":
            self.meta_lbl.setText(size_s)
            self.info_lbl.setText("Waiting in queue")
            self.act_toggle.setIcon(make_icon("play", "#22c55e", 16))
            self.act_toggle.setToolTip("Start now")
        elif state == "completed":
            self.meta_lbl.setText(size_s)
            verified = "  •  Verified" if getattr(job, "sha256", "") else ""
            self.info_lbl.setText(f"100%{verified}")
            self.act_toggle.setIcon(make_icon("play", "#475569", 16))
            toggle_enabled = False
        else:  # canceled and anything else terminal
            self.meta_lbl.setText(size_s)
            self.info_lbl.setText(f"{pct}%  •  Canceled")
            self.act_toggle.setIcon(make_icon("play", "#475569", 16))
            toggle_enabled = False
        self.act_toggle.setEnabled(toggle_enabled)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.job)
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.job)
        super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev):
        self.contextRequested.emit(self.job, ev.globalPos())


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
        self._cards_by_id = {}
        self._selected_id = None
        self._last_layout_ids = None
        self._speed_limit_mb = 0

        self._build_ui()
        self._setup_system_tray()

        # Update timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_rows)
        self.timer.start(350)

        # Background update check (5 seconds after startup)
        QTimer.singleShot(5000, lambda: self.check_updates_gui(manual=False))

    def _start_embedded_daemon(self):
        try:
            from . import daemon as daemon_mod
            daemon_mod.QM = self.qm
            httpd = daemon_mod.ThreadedHTTPServer(
                ("127.0.0.1", daemon_mod.HTTP_PORT),
                daemon_mod.HardenedApiHandler,
            )
            self._daemon_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._daemon_thread.start()
        except Exception as ex:
            print(f"[OpenDM] embedded daemon failed to start: {ex}", file=sys.stderr)

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
        from . import __version__
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ---- top bar ------------------------------------------------------
        topbar = QWidget()
        topbar.setObjectName("TopBar")
        t = QHBoxLayout(topbar)
        t.setContentsMargins(16, 10, 16, 10)
        t.setSpacing(10)

        brand_icon = QLabel()
        brand_icon.setPixmap(make_icon("app", "#3b82f6", 28).pixmap(28, 28))
        t.addWidget(brand_icon)
        brand = QLabel("OpenDM")
        brand.setObjectName("Brand")
        t.addWidget(brand)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("VersionTag")
        t.addWidget(ver)
        t.addSpacing(10)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchBox")
        self.search_box.setPlaceholderText("Search downloads…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(230)
        self.search_box.addAction(make_icon("search", "#64748b", 14),
                                  QLineEdit.LeadingPosition)
        self.search_box.textChanged.connect(self._on_search_changed)
        t.addWidget(self.search_box)

        t.addStretch(1)

        self.speed_pill = ClickableFrame()
        self.speed_pill.setObjectName("SpeedPill")
        self.speed_pill.setCursor(Qt.PointingHandCursor)
        self.speed_pill.setToolTip("Download speed limit — click to change")
        pill_lay = QHBoxLayout(self.speed_pill)
        pill_lay.setContentsMargins(12, 0, 12, 0)
        pill_lay.setSpacing(7)
        gauge = QLabel()
        gauge.setPixmap(make_icon("gauge", "#38bdf8", 14).pixmap(14, 14))
        pill_lay.addWidget(gauge)
        self.speed_slider_lbl = QLabel("Unlimited")
        self.speed_slider_lbl.setObjectName("SpeedPillLabel")
        pill_lay.addWidget(self.speed_slider_lbl)
        self.speed_pill.clicked.connect(self._open_speed_popover)
        t.addWidget(self.speed_pill)

        self.btn_pause_all = QPushButton("Pause All")
        self.btn_pause_all.setIcon(make_icon("pause", "#f59e0b", 16))
        self.btn_pause_all.setObjectName("GhostBtn")
        self.btn_pause_all.setCursor(Qt.PointingHandCursor)
        self.btn_pause_all.clicked.connect(self.pause_all)
        t.addWidget(self.btn_pause_all)

        self.btn_add = QPushButton("New Download")
        self.btn_add.setIcon(make_icon("add", "#ffffff", 16))
        self.btn_add.setObjectName("PrimaryBtn")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self.add_url)
        t.addWidget(self.btn_add)

        self.btn_gear = QToolButton()
        self.btn_gear.setObjectName("IconBtn")
        self.btn_gear.setIcon(make_icon("gear", "#94a3b8", 18))
        self.btn_gear.setCursor(Qt.PointingHandCursor)
        self.btn_gear.setToolTip("Menu")
        self.btn_gear.clicked.connect(self._open_gear_menu)
        t.addWidget(self.btn_gear)

        root_lay.addWidget(topbar)

        # ---- body: sidebar + main ------------------------------------------
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        side = QWidget()
        side.setObjectName("SideNav")
        side.setFixedWidth(224)
        s = QVBoxLayout(side)
        s.setContentsMargins(14, 16, 14, 16)
        s.setSpacing(2)

        lib_lbl = QLabel("LIBRARY")
        lib_lbl.setObjectName("SectionLabel")
        s.addWidget(lib_lbl)
        self.nav_items = {}
        for key, label, icon, color in LIBRARY_ITEMS:
            item = NavItem(key, label, icon=icon, color=color)
            item.clicked.connect(self.set_category)
            s.addWidget(item)
            self.nav_items[key] = item
        s.addSpacing(10)

        cat_lbl = QLabel("CATEGORIES")
        cat_lbl.setObjectName("SectionLabel")
        s.addWidget(cat_lbl)
        for key in ("Compressed", "Video", "Music", "Documents", "Programs"):
            label, color = CATEGORY_META[key]
            item = NavItem(key, label, icon=None, color=color)
            item.clicked.connect(self.set_category)
            s.addWidget(item)
            self.nav_items[key] = item

        s.addStretch(1)
        nav_settings = NavItem("settings", "Settings", icon="gear", color="#94a3b8",
                               show_badge=False)
        nav_settings.clicked.connect(lambda _k: self.open_settings())
        s.addWidget(nav_settings)
        nav_help = NavItem("help", "Help", icon="info", color="#94a3b8",
                           show_badge=False)
        nav_help.clicked.connect(lambda _k: self._show_help())
        s.addWidget(nav_help)

        self.nav_items["All"].set_selected(True)
        splitter.addWidget(side)

        # ---- main ------------------------------------------------------------
        main = QWidget()
        m = QVBoxLayout(main)
        m.setContentsMargins(22, 18, 22, 0)
        m.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("Downloads")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        self.subtitle_lbl = QLabel("")
        self.subtitle_lbl.setObjectName("Subtitle")
        head.addWidget(self.subtitle_lbl, 0, Qt.AlignBottom)
        head.addStretch(1)
        sort_lbl = QLabel("Sort:")
        sort_lbl.setObjectName("Subtitle")
        head.addWidget(sort_lbl)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Newest", "newest")
        self.sort_combo.addItem("Name", "name")
        self.sort_combo.addItem("Size", "size")
        self.sort_combo.addItem("Progress", "progress")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        head.addWidget(self.sort_combo)
        m.addLayout(head)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards_host = QWidget()
        self.cards_grid = QGridLayout(cards_host)
        self.cards_grid.setContentsMargins(2, 2, 2, 18)
        self.cards_grid.setSpacing(14)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        self.scroll_area.setWidget(cards_host)
        self.scroll_area.viewport().setStyleSheet("background: #0b0f19;")
        m.addWidget(self.scroll_area, 1)

        self.empty_widget = QWidget()
        e = QVBoxLayout(self.empty_widget)
        e.setAlignment(Qt.AlignCenter)
        e.setSpacing(10)
        e_icon = QLabel()
        e_icon.setPixmap(make_icon("app", "#3b82f6").pixmap(24, 24).scaled(
            72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        e_icon.setAlignment(Qt.AlignCenter)
        e.addWidget(e_icon)
        e_title = QLabel("No downloads yet")
        e_title.setObjectName("EmptyTitle")
        e_title.setAlignment(Qt.AlignCenter)
        e.addWidget(e_title)
        e_hint = QLabel("Click New Download or paste a download link to get started.")
        e_hint.setObjectName("EmptyHint")
        e_hint.setAlignment(Qt.AlignCenter)
        e_hint.setWordWrap(True)
        e.addWidget(e_hint)
        e.addSpacing(6)
        e_btn = QPushButton("New Download")
        e_btn.setIcon(make_icon("add", "#ffffff", 16))
        e_btn.setObjectName("PrimaryBtn")
        e_btn.setCursor(Qt.PointingHandCursor)
        e_btn.clicked.connect(self.add_url)
        e_btn_row = QHBoxLayout()
        e_btn_row.addStretch(1)
        e_btn_row.addWidget(e_btn)
        e_btn_row.addStretch(1)
        e.addLayout(e_btn_row)
        m.addWidget(self.empty_widget, 1)
        self.empty_widget.setVisible(False)

        splitter.addWidget(main)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root_lay.addWidget(splitter, 1)

        # ---- status bar --------------------------------------------------------
        self.status_bar = QStatusBar()
        self.stat_label = QLabel("Ready • Engine Online")
        self.stat_label.setObjectName("StatusMain")
        self.status_bar.addWidget(self.stat_label, 1)
        self.free_label = QLabel("")
        self.free_label.setObjectName("StatusDim")
        self.status_bar.addPermanentWidget(self.free_label)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet("background: #22c55e; border-radius: 4px;")
        self.status_bar.addPermanentWidget(dot)
        eng = QLabel("Engine Online")
        eng.setObjectName("StatusDim")
        self.status_bar.addPermanentWidget(eng)
        self.setStatusBar(self.status_bar)

        self.refresh_rows()

    def set_category(self, key: str):
        if key not in self.nav_items:
            return
        self.selected_category = key
        for k, item in self.nav_items.items():
            item.set_selected(k == key)
        self.refresh_rows(force_refilter=True)


    def _show_context_menu(self, job, global_pos):
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

        menu.exec(global_pos)


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
        self._speed_limit_mb = val
        if val == 0:
            self.speed_slider_lbl.setText("Unlimited")
            limit_bytes = 0
        else:
            self.speed_slider_lbl.setText(f"{val} MB/s")
            limit_bytes = val * 1024 * 1024
        # Wire straight into the QueueManager's shared token bucket so the
        # limit applies live to every job (0 = unlimited).
        self.qm.set_speed_limit(limit_bytes)


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

        auto_update_cb = QCheckBox("Automatically check for updates on startup")
        auto_update_cb.setChecked(getattr(self, "auto_check_updates", True))
        form.addRow("", auto_update_cb)

        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec():
            p_val = proxy_in.text().strip()
            self.qm.proxy = p_val if p_val else None
            self.auto_check_updates = auto_update_cb.isChecked()
            QMessageBox.information(self, "Settings Saved", f"Proxy set to: {p_val or 'None (Direct connection)'}")

    def check_updates_gui(self, manual: bool = True):
        """Asynchronously query GitHub Releases API and display update dialog."""
        if not manual and not getattr(self, "auto_check_updates", True):
            return

        def _worker():
            try:
                from . import updater
                proxy = getattr(self.qm, "proxy", None)
                info = updater.check_for_updates(proxy=proxy)
                QTimer.singleShot(0, lambda: self._handle_update_result(info, manual))
            except Exception as ex:
                if manual:
                    QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Update Error", f"Failed to check updates:\n{ex}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_update_result(self, info: dict | None, manual: bool):
        if not info or info.get("error"):
            if manual:
                err = info.get("error") if info else "Unknown error"
                QMessageBox.warning(self, "Update Check Failed", f"Could not check for updates:\n{err}")
            return

        from . import __version__
        tag = info.get("tag_name", "")
        if not info.get("is_newer"):
            if manual:
                QMessageBox.information(self, "Latest Version", f"You are running the latest version (v{__version__}).")
            return

        # Display Update Dialog
        dlg = self._build_update_offer_dialog(info)
        dlg.exec()

    def _build_update_offer_dialog(self, info: dict) -> QDialog:
        """Build the update-offer dialog WITHOUT showing it (kept separate
        from _handle_update_result so tests can inspect it headless).

        When the release publishes no SHA-256 checksum, no auto-install is
        offered: the release URL is shown instead so the user can review
        and download the installer manually.
        """
        from . import __version__
        tag = info.get("tag_name", "")
        expected_sha = info.get("expected_sha256")
        installer_url = info.get("installer_url")
        html_url = info.get("html_url", "")

        dlg = QDialog(self)
        dlg.setWindowTitle("Software Update Available — OpenDM")
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)

        badge_box = QFrame()
        badge_box.setStyleSheet("background: #0f172a; border-radius: 8px; padding: 12px;")
        b_lay = QVBoxLayout(badge_box)
        v_lbl = QLabel(f"<b>A new version is available!</b> &nbsp; Current: <code>v{__version__}</code> &nbsp;➔&nbsp; <span style='color:#38bdf8; font-weight:bold;'>{tag}</span>")
        v_lbl.setStyleSheet("font-size: 13px; color: #f8fafc;")
        b_lay.addWidget(v_lbl)
        if expected_sha:
            # Pre-download: the checksum is only *expected*, never "verified".
            sha_lbl = QLabel(f"<span style='color: #38bdf8;'>{update_sha_offer_line(expected_sha)}</span>")
            sha_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")
            b_lay.addWidget(sha_lbl)
        lay.addWidget(badge_box)

        lay.addWidget(QLabel("Release Notes:"))
        notes_box = QPlainTextEdit()
        notes_box.setReadOnly(True)
        notes_box.setPlainText(info.get("body", "No release notes provided."))
        notes_box.setStyleSheet("background: #0f172a; color: #cbd5e1; font-family: sans-serif; font-size: 12px; border: 1px solid #1e293b; border-radius: 6px;")
        notes_box.setFixedHeight(180)
        lay.addWidget(notes_box)

        if not expected_sha:
            notice = QLabel(update_no_hash_notice_line(html_url))
            notice.setWordWrap(True)
            notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
            notice.setStyleSheet("font-size: 11px; color: #94a3b8;")
            lay.addWidget(notice)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        later_btn = QPushButton("Later")
        later_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(later_btn)

        if expected_sha:
            install_btn = QPushButton("Download & Update Now")
            install_btn.setObjectName("install_btn")
            install_btn.setStyleSheet("background: #2563eb; color: white; font-weight: bold; padding: 8px 18px; border-radius: 6px;")
            btn_row.addWidget(install_btn)

            def _do_update():
                dlg.accept()
                if not installer_url:
                    QMessageBox.warning(self, "No Installer Asset", "OpenDM-Setup.exe asset was not found in release.")
                    return
                self._start_update_download(installer_url, tag, expected_sha)

            install_btn.clicked.connect(_do_update)
        lay.addLayout(btn_row)
        return dlg

    def _start_update_download(self, url: str, tag: str, expected_sha: str | None):
        from . import updater
        target_path = updater.get_update_target_path(tag)
        dest_dir = os.path.dirname(target_path)
        filename = os.path.basename(target_path)

        # Enqueue update download directly into OpenDM engine
        job = self.qm.add(
            url=url,
            conns=4,
            dest_dir=dest_dir,
            suggested_filename=filename,
        )
        if expected_sha:
            job.expected_sha256 = expected_sha

        def _on_job_complete(_job):
            # Runs on the download worker thread: never touch Qt widgets
            # here. Marshal the verification/install prompts onto the Qt
            # GUI thread.
            QTimer.singleShot(
                0,
                lambda: self._finish_update_download(job, target_path, tag, expected_sha),
            )

        job.add_on_complete(_on_job_complete)

        job.start()
        self.refresh_rows()
        QMessageBox.information(
            self, "Update Started",
            f"Downloading {filename} via OpenDM accelerated engine.\nYou will be prompted to install once verified."
        )

    def _finish_update_download(self, job, target_path: str, tag: str, expected_sha: str | None):
        """GUI-thread handler for a finished update download.

        Verifies the installer SHA-256 before offering to install it.
        Must only ever run on the Qt GUI thread (it is marshaled there by
        _start_update_download via QTimer.singleShot).
        """
        from . import updater
        if updater.verify_installer(target_path, expected_sha):
            ret = QMessageBox.question(
                self, "Update Downloaded",
                f"OpenDM {tag} has been downloaded and verified successfully.\n\nInstall update now?",
                QMessageBox.Yes | QMessageBox.No
            )
            if ret == QMessageBox.Yes:
                self._launch_installer_and_quit(target_path)
        else:
            try:
                os.remove(target_path)
            except OSError:
                pass
            QMessageBox.critical(
                self, "Update Verification Failed",
                "The downloaded update failed SHA-256 verification and has been deleted.\n\nThe installer will NOT be launched."
            )

    def _launch_installer_and_quit(self, installer_path: str):
        from . import updater
        def _clean_exit():
            self.qm._save()
            QApplication.quit()

        updater.execute_installer_and_exit(installer_path, on_before_exit=_clean_exit)

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
        if not self._selected_id:
            return None
        for j in self.qm.jobs:
            if j.id == self._selected_id:
                return j
        return None

    def _update_actions_state(self):
        self.btn_pause_all.setEnabled(
            any(j.state == "downloading" for j in self.qm.jobs)
        )


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

    def _on_card_clicked(self, job):
        self._selected_id = job.id
        for jid, card in self._cards_by_id.items():
            card.set_selected(jid == job.id)
        self._update_actions_state()

    def _on_card_double_clicked(self, job):
        self._on_card_clicked(job)
        DetailsDialog(job, self).exec()

    def _on_card_context(self, job, global_pos):
        self._on_card_clicked(job)
        self._show_context_menu(job, global_pos)

    def _on_card_action(self, job, action):
        if action == "toggle":
            if job.state == "downloading":
                job.pause()
            elif job.state in ("paused", "error", "queued"):
                job.resume()
        elif action == "folder":
            self._open_file(os.path.dirname(job.dest) if job.dest else self.qm.dest_dir)
        elif action == "cancel":
            job.cancel()
        self.refresh_rows()
        self._update_actions_state()

    def _on_search_changed(self, _text):
        self.refresh_rows(force_refilter=True)

    def _on_sort_changed(self, _index):
        self.refresh_rows()

    def pause_all(self):
        for j in list(self.qm.jobs):
            if j.state == "downloading":
                j.pause()
        self.refresh_rows()
        self._update_actions_state()

    def _open_speed_popover(self):
        pop = QDialog(self, Qt.Popup)
        pop.setWindowTitle("Speed Limit")
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Download speed limit:"))
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for label, mb in (("Unlimited", 0), ("1 MB/s", 1), ("5 MB/s", 5), ("20 MB/s", 20)):
            b = QPushButton(label)
            b.setObjectName("ChipBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(
                lambda _c, v=mb: (self._on_speed_slider_changed(v), pop.accept())
            )
            chips.addWidget(b)
        lay.addLayout(chips)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(self._speed_limit_mb)
        slider.valueChanged.connect(self._on_speed_slider_changed)
        lay.addWidget(slider)
        hint = QLabel("0 = unlimited, up to 100 MB/s — applies instantly to all downloads.")
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        pop.move(self.speed_pill.mapToGlobal(QPoint(0, self.speed_pill.height() + 6)))
        pop.exec()

    def _open_gear_menu(self):
        menu = QMenu(self)
        act_settings = menu.addAction("Network && Proxy Settings…")
        act_settings.triggered.connect(self.open_settings)
        act_update = menu.addAction("Check for Updates")
        act_update.triggered.connect(lambda: self.check_updates_gui(manual=True))
        menu.addSeparator()
        act_about = menu.addAction("About OpenDM")
        act_about.triggered.connect(self._show_help)
        menu.exec(self.btn_gear.mapToGlobal(QPoint(0, self.btn_gear.height())))

    def _show_help(self):
        from . import __version__
        QMessageBox.about(
            self, "About OpenDM",
            f"<b>OpenDM v{__version__}</b><br><br>"
            "Advanced download manager with multi-connection segmented downloads, "
            "browser integration and automatic updates.<br><br>"
            "Repository: github.com/shadowcerdic/OpenDM",
        )


    def refresh_rows(self, force_refilter=False):
        jobs = list(self.qm.jobs)

        counts = {"All": len(jobs), "Active": 0, "Completed": 0, "Queued": 0,
                  "Compressed": 0, "Video": 0, "Music": 0, "Documents": 0, "Programs": 0}
        for j in jobs:
            st = (j.state or "").lower()
            if st == "downloading":
                counts["Active"] += 1
            elif st == "completed":
                counts["Completed"] += 1
            elif st == "queued":
                counts["Queued"] += 1
            if j.category in counts:
                counts[j.category] += 1
        for key, item in self.nav_items.items():
            if key in counts:
                item.set_count(counts[key])

        q = self.search_box.text().strip().lower()
        cat = self.selected_category
        filtered = []
        for j in jobs:
            st = (j.state or "").lower()
            if cat == "Active":
                if st != "downloading":
                    continue
            elif cat == "Completed":
                if st != "completed":
                    continue
            elif cat == "Queued":
                if st != "queued":
                    continue
            elif cat != "All" and j.category != cat:
                continue
            if q and q not in j.filename.lower() and q not in (j.url or "").lower():
                continue
            filtered.append(j)

        mode = self.sort_combo.currentData()
        if mode == "name":
            filtered.sort(key=lambda j: j.filename.lower())
        elif mode == "size":
            filtered.sort(key=lambda j: j.total or 0, reverse=True)
        elif mode == "progress":
            filtered.sort(
                key=lambda j: (j.done / j.total) if j.total else 0, reverse=True
            )

        seen = set()
        for j in filtered:
            seen.add(j.id)
            card = self._cards_by_id.get(j.id)
            if card is None:
                card = DownloadCard(j)
                card.clicked.connect(self._on_card_clicked)
                card.doubleClicked.connect(self._on_card_double_clicked)
                card.contextRequested.connect(self._on_card_context)
                card.actionRequested.connect(self._on_card_action)
                card.set_selected(j.id == self._selected_id)
                self._cards_by_id[j.id] = card
            else:
                card.refresh(j)
        for jid in list(self._cards_by_id.keys()):
            if jid not in seen:
                card = self._cards_by_id.pop(jid)
                self.cards_grid.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
                if self._selected_id == jid:
                    self._selected_id = None

        layout_ids = [j.id for j in filtered]
        if layout_ids != self._last_layout_ids:
            for r in range(self.cards_grid.rowCount()):
                self.cards_grid.setRowStretch(r, 0)
            while self.cards_grid.count():
                self.cards_grid.takeAt(0)
            for i, j in enumerate(filtered):
                self.cards_grid.addWidget(self._cards_by_id[j.id], i // 2, i % 2)
            # Empty trailing row absorbs leftover vertical space so cards keep
            # their natural height instead of stretching to fill the viewport.
            self.cards_grid.setRowStretch((len(filtered) + 1) // 2, 1)
            self._last_layout_ids = layout_ids

        has = bool(filtered)
        self.scroll_area.setVisible(has)
        self.empty_widget.setVisible(not has)
        self.subtitle_lbl.setText(f"Showing {len(filtered)} of {len(jobs)}")

        self.stat_label.setText(
            f"Total: {len(jobs)}  •  Active: {counts['Active']}  •  "
            f"Completed: {counts['Completed']}  •  Directory: {self.qm.dest_dir}"
        )
        try:
            free = shutil.disk_usage(self.qm.dest_dir).free
            self.free_label.setText(f"Free space: {_fmt_size(free)}")
        except OSError:
            self.free_label.setText("")
        self._update_actions_state()



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
/* ---- Card-layout redesign additions ---- */
#TopBar {
    background-color: #0f172a;
    border-bottom: 1px solid #1e293b;
}
#Brand {
    font-size: 17px;
    font-weight: 800;
    color: #f8fafc;
}
#VersionTag {
    font-size: 11px;
    color: #64748b;
    background: #1e293b;
    border-radius: 8px;
    padding: 2px 8px;
}
#SearchBox {
    background-color: #1a2334;
    border: 1px solid #243049;
    border-radius: 8px;
    padding: 7px 12px;
    color: #e2e8f0;
}
#SearchBox:focus {
    border: 1px solid #3b82f6;
}
#SpeedPill {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 14px;
    padding: 6px 4px;
    color: #e2e8f0;
}
#SpeedPill:hover {
    border: 1px solid #3b82f6;
}
#SpeedPillLabel {
    color: #38bdf8;
    font-weight: 600;
    font-size: 12px;
    background: transparent;
    border: none;
}
#GhostBtn {
    background: transparent;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 7px 14px;
    color: #e2e8f0;
}
#GhostBtn:hover {
    border-color: #f59e0b;
}
#GhostBtn:disabled {
    color: #475569;
    border-color: #1e293b;
}
#PrimaryBtn {
    background: #2563eb;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    color: white;
    font-weight: 700;
}
#PrimaryBtn:hover {
    background: #1d4ed8;
}
#IconBtn {
    background: transparent;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 7px;
}
#IconBtn:hover {
    border-color: #3b82f6;
}
#SideNav {
    background-color: #0d1322;
    border-right: 1px solid #1e293b;
}
#SectionLabel {
    font-size: 11px;
    font-weight: 700;
    color: #64748b;
    padding: 0 10px 6px 10px;
}
#NavItem {
    border-radius: 8px;
}
#NavItem:hover {
    background: #162032;
}
#NavItem[selected="true"] {
    background: #1d2b4a;
}
#NavItem[selected="true"] QLabel {
    color: #f8fafc;
    font-weight: 600;
}
#NavBadge {
    background: #243049;
    color: #94a3b8;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 600;
}
#NavItem[selected="true"] #NavBadge {
    background: #2563eb;
    color: white;
}
#PageTitle {
    font-size: 20px;
    font-weight: 800;
    color: #f8fafc;
}
#Subtitle {
    font-size: 12px;
    color: #64748b;
}
QScrollArea {
    background: transparent;
    border: none;
}
#Card {
    background: #111a2e;
    border: 1px solid #1e2a44;
    border-radius: 12px;
}
#Card:hover {
    border: 1px solid #2c3d63;
}
#Card[selected="true"] {
    border: 1px solid #3b82f6;
}
#CardName {
    font-size: 13px;
    font-weight: 600;
    color: #f1f5f9;
    background: transparent;
    border: none;
}
#CardMeta {
    font-size: 12px;
    color: #7d8ba3;
    background: transparent;
    border: none;
}
#CardInfo {
    font-size: 12px;
    color: #38bdf8;
    font-weight: 600;
    background: transparent;
    border: none;
}
#CardAction {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 5px;
}
#CardAction:hover {
    background: #1e293b;
}
#EmptyTitle {
    font-size: 16px;
    font-weight: 700;
    color: #e2e8f0;
}
#EmptyHint {
    font-size: 13px;
    color: #64748b;
}
#ChipBtn {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 5px 12px;
    font-size: 12px;
}
#ChipBtn:hover {
    border-color: #3b82f6;
    color: #38bdf8;
}
#StatusMain {
    color: #94a3b8;
    font-size: 12px;
}
#StatusDim {
    color: #64748b;
    font-size: 12px;
}
QStatusBar {
    background: #0d1322;
    border-top: 1px solid #1e293b;
}
QComboBox {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 5px 10px;
    color: #e2e8f0;
}
QComboBox QAbstractItemView {
    background: #1e293b;
    color: #e2e8f0;
    selection-background-color: #2563eb;
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
