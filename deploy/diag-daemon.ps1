# Diagnose standalone daemon start on Windows
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'

# try visible python (not pythonw) to catch the error
Set-Location "$root\app"
$env:PYTHONPATH = "$root\app"
$out = & "$root\python\python.exe" -m opendm.daemon 2>&1 | Select-Object -First 20
# this blocks; instead run as job with timeout
Write-Output "direct-run-blocks (expected for a server). Using Start-Process instead."

# check what happened with pythonw earlier
Write-Output "== event log for pythonw crashes =="
Get-WinEvent -LogName Application -MaxEvents 20 -ErrorAction SilentlyContinue |
  Where-Object { $_.ProviderName -eq 'Application Error' -and $_.Message -match 'python' } |
  Select-Object -First 3 TimeCreated, Message | ForEach-Object { Write-Output "EVT: $($_.TimeCreated) :: $($_.Message.Substring(0,[Math]::Min(200,$_.Message.Length)))" }

Write-Output "== try Start-Process with cmd wrapper and env =="
$cmd = "cmd /c set PYTHONPATH=C:\OpenDM\app&& cd /d C:\OpenDM\app&& C:\OpenDM\python\python.exe -m opendm.daemon > C:\OpenDM\daemon.out.log 2>&1"
Start-Process cmd -ArgumentList '/c', "set PYTHONPATH=C:\OpenDM\app&& cd /d C:\OpenDM\app&& C:\OpenDM\python\python.exe -m opendm.daemon > C:\OpenDM\daemon.out.log 2>&1" -WindowStyle Hidden
Start-Sleep -Seconds 4
Write-Output "DAEMON-PORT: $((Test-NetConnection 127.0.0.1 -Port 10010 -WarningAction SilentlyContinue).TcpTestSucceeded)"
if (Test-Path 'C:\OpenDM\daemon.out.log') { Get-Content 'C:\OpenDM\daemon.out.log' | Select-Object -First 10 | ForEach-Object { Write-Output "LOG: $_" } }
