# Diagnose 2: check site-packages import path issue in embedded python
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'
Set-Location "$root\app"

Write-Output "== python ._pth content =="
Get-Content "$root\python\python313._pth" | ForEach-Object { Write-Output "PTH: $_" }

Write-Output "== sys.path check =="
& "$root\python\python.exe" -c "import sys; print(sys.path)" 2>&1 | ForEach-Object { Write-Output "PATH: $_" }

Write-Output "== can it import opendm when cwd = app? =="
$env:PYTHONPATH = "$root\app"
& "$root\python\python.exe" -c "import os; os.chdir(r'C:\OpenDM\app'); import sys; sys.path.insert(0,''); import opendm; print('IMPORT-OK', opendm.__file__)" 2>&1 | ForEach-Object { Write-Output "IMP: $_" }

Write-Output "== start daemon with explicit sys.path injection via -c =="
Start-Process "$root\python\python.exe" -ArgumentList '-c', "import sys; sys.path.insert(0, r'C:\OpenDM\app'); import opendm.daemon as d; d.main()" -WindowStyle Hidden
Start-Sleep -Seconds 4
Write-Output "DAEMON-PORT: $((Test-NetConnection 127.0.0.1 -Port 10010 -WarningAction SilentlyContinue).TcpTestSucceeded)"
