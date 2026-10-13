# Smoke-test OpenDM on Windows: run headless-ish GUI test + engine test
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'
Set-Location "$root\app"
$env:PYTHONPATH = "$root\app"

# 1. engine test (real download through Windows networking)
& "$root\python\python.exe" -c @"
import sys, hashlib, os, time
sys.path.insert(0, r'$root\app')
from opendm import engine
t0 = time.time()
res = engine.download('http://speedtest.tele2.net/10MB.zip', dest=os.path.expanduser('~/Downloads/odm-test-10mb.zip'), conns=4)
dur = time.time() - t0
print('WIN-ENGINE-TEST:', res)
import urllib.request
urllib.request.urlretrieve('http://speedtest.tele2.net/10MB.zip', os.path.expanduser('~/Downloads/odm-ref-10mb.zip'))
h1 = hashlib.sha256(open(os.path.expanduser('~/Downloads/odm-test-10mb.zip'),'rb').read()).hexdigest()
h2 = hashlib.sha256(open(os.path.expanduser('~/Downloads/odm-ref-10mb.zip'),'rb').read()).hexdigest()
print('WIN-HASH-MATCH:', h1 == h2)
"@ 2>&1 | ForEach-Object { Write-Output "ENGINE: $_" }

# 2. GUI import + offscreen construction test
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
print('WIN-GUI-CONSTRUCTED: OK  daemon-embedded:', win._daemon_thread is not None)
"@ 2>&1 | ForEach-Object { Write-Output "GUI: $_" }

# 3. daemon API live check (embedded in GUI is gone; test standalone binding)
& "$root\python\python.exe" -c @"
import sys, threading, json, urllib.request
sys.path.insert(0, r'$root\app')
from opendm import daemon
from http.server import HTTPServer
httpd = HTTPServer(('127.0.0.1', daemon.HTTP_PORT), daemon.HardenedApiHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
req = urllib.request.Request('http://127.0.0.1:10010/api/v1/status', headers={'X-OpenDM-Token': daemon.TOKEN})
r = urllib.request.urlopen(req, timeout=5)
print('WIN-DAEMON-API:', r.status, r.read().decode())
"@ 2>&1 | ForEach-Object { Write-Output "DAEMON: $_" }
Write-Output "WIN-SMOKE-DONE"
