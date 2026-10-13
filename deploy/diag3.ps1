# Diagnose 3: run daemon -c in FOREGROUND briefly to catch its error
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'
& "$root\python\python.exe" -c "import sys; sys.path.insert(0, r'C:\OpenDM\app'); import opendm.daemon as d; d.main()" 2>&1 |
  Select-Object -First 15 | ForEach-Object { Write-Output "FG: $_" }
