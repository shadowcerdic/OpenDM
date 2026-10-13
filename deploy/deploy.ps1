# Deploy OpenDM v1.2 to Windows: install embedded Python + deps, copy repo,
# write launcher, register autostart (Run key), load test.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$root    = 'C:\OpenDM'
$pyDir   = "$root\python"
$repoDir = "$root\app"
$dl       = "$root\_install"
New-Item -ItemType Directory -Force -Path $root, $dl | Out-Null

# 1. embedded Python 3.13 (official, no admin install needed)
$pyZip = "$dl\python-embed.tar.gz"
$pyUrl = "https://www.python.org/ftp/python/3.13.2/python-3.13.2-embed-win32.zip"
# use amd64 for this machine
$pyUrl = "https://www.python.org/ftp/python/3.13.2/python-3.13.2-embed-amd64.zip"
$pyZip = "$dl\py-embed.zip"
if (-not (Test-Path "$pyDir\python.exe")) {
    & curl.exe -L --fail -sS -o $pyZip $pyUrl
    Write-Output "PY-ZIP: $((Get-Item $pyZip).Length) bytes"
    Expand-Archive -LiteralPath $pyZip -DestinationPath $pyDir -Force
}
Write-Output "PYTHON: $(& "$pyDir\python.exe" --version 2>&1)"

# 2. pip into embedded python (get-pip)
if (-not (Test-Path "$pyDir\Scripts\pip.exe")) {
    & curl.exe -L --fail -sS -o "$dl\get-pip.py" https://bootstrap.pypa.io/get-pip.py
    & "$pyDir\python.exe" "$dl\get-pip.py" --no-warn-script-location 2>&1 | Select-Object -Last 2
}
# enable import site
$pth = "$pyDir\python313._pth"
if (Test-Path $pth) {
    (Get-Content $pth) -replace '^#import site', 'import site' | Set-Content $pth
}

# 3. deps
& "$pyDir\python.exe" -m pip install --no-warn-script-location -q PySide6 websockets 2>&1 | Select-Object -Last 2

# 4. copy app (assumes repo was pushed to C:\OpenDM\_install\app by scp before)
Write-Output "REPO: $repoDir"
if (Test-Path "$repoDir\opendm\gui.py") { Write-Output "APP-FILES-OK" } else { Write-Output "APP-FILES-MISSING (scp step)" }
