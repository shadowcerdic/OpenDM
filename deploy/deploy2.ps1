# Stage 2: install deps (visible), move repo into place, write launcher + autostart
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$root = 'C:\OpenDM'

# 1. move repo into app/
if (-not (Test-Path "$root\app\opendm\gui.py")) {
    if (Test-Path "$root\_install\opendm\gui.py") {
        Move-Item -LiteralPath "$root\_install" "$root\app" -Force
    }
}
Write-Output "APP: $(Test-Path "$root\app\opendm\gui.py")"

# 2. deps — visible output
& "$root\python\python.exe" -m pip install --no-warn-script-location PySide6 websockets 2>&1 |
    Select-Object -Last 4 | ForEach-Object { Write-Output "PIP: $_" }

& "$root\python\python.exe" -c "import PySide6, websockets; print('DEPS-OK', PySide6.__version__)" 2>&1 |
    ForEach-Object { Write-Output "CHECK: $_" }

# 3. launcher
@"
`$ErrorActionPreference = 'Continue'
`$py = 'C:\OpenDM\python\pythonw.exe'
if (-not (Test-Path `$py)) { `$py = 'C:\OpenDM\python\python.exe' }
Set-Location C:\OpenDM\app
`$env:PYTHONPATH = 'C:\OpenDM\app'
& `$py -m opendm.gui
"@ | Set-Content -LiteralPath "$root\run-opendm.ps1" -Encoding ASCII
Write-Output "LAUNCHER: $(Test-Path "$root\run-opendm.ps1")"

# 4. shortcut in Startup (autostart at logon)
$ws = New-Object -ComObject WScript.Shell
$startup = [Environment]::GetFolderPath('Startup')
$lnk = $ws.CreateShortcut("$startup\OpenDM.lnk")
$lnk.TargetPath = "$root\python\pythonw.exe"
$lnk.Arguments = "-m opendm.gui"
$lnk.WorkingDirectory = "$root\app"
$lnk.WindowStyle = 7   # minimized
$lnk.Save()
Write-Output "AUTOSTART-LNK: $(Test-Path "$startup\OpenDM.lnk")"
Write-Output "STAGE2-DONE"
