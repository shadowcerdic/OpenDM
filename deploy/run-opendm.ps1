# OpenDM launcher for Windows — starts the GUI (embedded daemon) hidden-console
$ErrorActionPreference = 'Continue'
$py  = 'C:\OpenDM\python\pythonw.exe'
$app = 'C:\OpenDM\app'
if (-not (Test-Path $py)) { $py = 'C:\OpenDM\python\python.exe' }
Set-Location $app
$env:PYTHONPATH = $app
$env:PYTHONUNBUFFERED = '1'
& $py -m opendm.gui
