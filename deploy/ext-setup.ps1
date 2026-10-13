# Load the OpenDM extension into Chrome via a dedicated profile and verify via
# the Chrome DevTools protocol-free approach: check the extension process + prefs.
# (Loading an unpacked extension requires chrome://extensions UI OR a policy;
#  we use the registry policy for "ExtensionInstallForcelist"? No - that needs
#  a packed CRX + update URL. Instead: launch Chrome with --load-extension.)
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'

# 1. copy the extension into place
if (-not (Test-Path "$root\extension\manifest.json")) {
    Write-Output "extension files missing"
    exit 1
}

# 2. launch Chrome with our extension loaded, isolated profile
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { $chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" }
if (-not (Test-Path $chrome)) { Write-Output "CHROME-NOT-FOUND"; exit 1 }

$prof = "$root\chrome-profile"
Start-Process $chrome -ArgumentList @(
    "--user-data-dir=$prof",
    "--load-extension=$root\extension",
    "--no-first-run", "--no-default-browser-check",
    "about:blank"
)
Write-Output "CHROME-LAUNCHED with extension"
Start-Sleep -Seconds 6

# 3. verify the extension service worker is alive (process cmdline check)
$procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -match 'extension' -or $_.CommandLine -match 'service_worker' } |
    Select-Object -First 3 ProcessId, CommandLine
foreach ($p in $procs) { Write-Output "EXT-PROC: $($p.ProcessId) :: $($p.CommandLine.Substring(0, [Math]::Min(120, $p.CommandLine.Length)))" }

# 4. confirm the daemon port is reachable from this machine
$t = Test-NetConnection 127.0.0.1 -Port 10010 -WarningAction SilentlyContinue
Write-Output "DAEMON-PORT-10010: $($t.TcpTestSucceeded)"

# 5. run the daemon standalone so the extension can talk to it (background)
Start-Process "$root\python\pythonw.exe" -ArgumentList "-m","opendm.daemon" -WorkingDirectory "$root\app" -WindowStyle Hidden
Start-Sleep -Seconds 3
$t2 = Test-NetConnection 127.0.0.1 -Port 10010 -WarningAction SilentlyContinue
Write-Output "DAEMON-AFTER-START: $($t2.TcpTestSucceeded)"
Write-Output "EXT-SETUP-DONE"
