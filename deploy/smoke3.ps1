# Smoke-test round 3: engine on Windows using GitHub-hosted file (443 reachable)
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'
Set-Location "$root\app"
$env:PYTHONPATH = "$root\app"

& "$root\python\python.exe" -c @"
import sys, os, hashlib
sys.path.insert(0, r'$root\app')
from opendm import engine

url = 'https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-pc-windows-msvc.zip'
dest = os.path.expanduser('~/Downloads/odm-ripgrep.zip')
print('probing...')
meta = engine.probe(url)
print('PROBE:', meta)
res = engine.download(url, dest=dest, conns=4)
print('WIN-ENGINE-OK:', res)
h = hashlib.sha256(open(dest,'rb').read()).hexdigest()
print('LOCAL-SHA:', h[:16])
"@ 2>&1 | ForEach-Object { Write-Output "ENGINE: $_" }

# verify hash matches VPS-computed reference (passed out-of-band)
Write-Output "size: $((Get-Item "$env:USERPROFILE\Downloads\odm-ripgrep.zip" -ErrorAction SilentlyContinue).Length)"
Write-Output "SMOKE3-DONE"
