# Restart OpenDM GUI with the fixed UI logic
Get-Process pythonw, python -ErrorAction SilentlyContinue | Where-Object {
    $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
    $cl -match 'opendm'
} | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

Start-Process "C:\OpenDM\python\pythonw.exe" -ArgumentList "-m opendm.gui" -WorkingDirectory "C:\OpenDM\app"
Start-Sleep -Seconds 2
Write-Output "RESTARTED_OK"
Get-Process pythonw -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
