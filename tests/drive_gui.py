"""Headless demo driver: launches the OpenDM GUI with real downloads and
captures screenshots at key moments. Run under Xvfb."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from opendm.gui import MainWindow, DetailsDialog
from opendm.queue import QueueManager

SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots")
os.makedirs(SHOTS, exist_ok=True)

URLS = [
    ("http://speedtest.tele2.net/10MB.zip", 4),
    ("https://proof.ovh.net/files/10Mb.dat", 6),
    ("http://speedtest.tele2.net/1MB.zip", 2),
]

from opendm.gui import DARK_QSS
app = QApplication(sys.argv)
app.setStyleSheet(DARK_QSS)
app.setApplicationName("OpenDM")
win = MainWindow()

def shot(name):
    pix = win.grab()
    path = os.path.join(SHOTS, name)
    pix.save(path)
    print("saved", path, pix.width(), "x", pix.height())

# T+0: show empty window
win.show()
QTimer.singleShot(800, lambda: (shot("01-main-window.png"), add_jobs()))

def add_jobs():
    for url, conns in URLS:
        j = win.qm.add(url, conns=conns)
        j.start()
    # mid-flight screenshot while downloads are running
    QTimer.singleShot(2500, lambda: shot("02-downloading.png"))
    # details dialog open on the first job, mid-download
    QTimer.singleShot(3800, open_details)

def open_details():
    job = win.qm.jobs[0]
    dlg = DetailsDialog(job, win)
    dlg.show()
    QTimer.singleShot(900, lambda: (shot("03-details-segments.png"), dlg.close()))
    QTimer.singleShot(5000, finish)

def finish():
    shot("04-completed.png")
    for j in win.qm.jobs:
        print("JOB", j.id, j.state, j.total, j.sha256[:16] if j.sha256 else None)
    app.quit()

QTimer.singleShot(20000, app.quit)  # hard stop
app.exec()
print("driver done")
