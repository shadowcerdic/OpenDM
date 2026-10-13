# Smoke-test round 2: fixed STATES import + alternate test URL
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'
Set-Location "$root\app"
$env:PYTHONPATH = "$root\app"

# reachable test file from Iran (proof.ovh.net worked earlier from VPS; try both)
& "$root\python\python.exe" -c @"
import sys, os
sys.path.insert(0, r'$root\app')
from opendm import engine
for url in ['http://speedtest.tele2.net/1MB.zip', 'https://proof.ovh.net/files/1Mb.dat']:
    try:
        res = engine.download(url, dest=os.path.expanduser('~/Downloads/odm-smoke.bin'), conns=2)
        print('WIN-ENGINE-OK:', url, res)
        break
    except Exception as ex:
        print('WIN-ENGINE-FAIL:', url, repr(ex)[:120])
"@ 2>&1 | ForEach-Object { Write-Output "ENGINE: $_" }

# GUI test with fix
$env:QT_QPA_PLATFORM = 'offscreen'
& "$root\python\python.exe" -c @"
import sys, os
sys.path.insert(0, r'$root\app')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from opendm.gui import MainWindow
app = QApplication([])
win = MainWindow()
win.show()
app.processEvents()
print('WIN-GUI-CONSTRUCTED: OK  embedded-daemon-thread:', win._daemon_thread is not None)
win.close()
"@ 2>&1 | ForEach-Object { Write-Output "GUI: $_" }

# grab a screenshot via real GUI on the interactive desktop
$env:QT_QPA_PLATFORM = 'windows'
& "$root\python\python.exe" -c @"
import sys, os
sys.path.insert(0, r'$root\app')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from opendm.gui import MainWindow, DARK_QSS
from opendm.queue import Job
app = QApplication([])
app.setStyleSheet(DARK_QSS)
win = MainWindow()
win.show()
app.processEvents()

class FakeJob:
    url='https://proof.ovh.net/files/10Mb.dat'; filename='10Mb.dat'; category='General'
    state='downloading'; total=10485760; done=7300000; speed_avg=3.1e6; conns=4
    sha256=None; error=None
    parts=[{'start':0,'end':2621439,'have':2621439},{'start':2621440,'end':5242879,'have':2100000},
           {'start':5242880,'end':7864319,'have':1578000},{'start':7864320,'end':10485759,'have':1001780}]
win.qm.jobs=[FakeJob()]
win.refresh_rows()
app.processEvents()
QTimer.singleShot(1200, lambda: (win.grab().save(r'C:\OpenDM\win-gui-proof.png'), print('SCREENSHOT-SAVED'), app.quit()))
app.exec()
"@ 2>&1 | ForEach-Object { Write-Output "SHOT: $_" }
Write-Output "if exist: $(Test-Path 'C:\OpenDM\win-gui-proof.png')"
Write-Output "SMOKE2-DONE"
