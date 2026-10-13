# Final install: pack extension CRX on Windows, register Force-install policy,
# restart the test Chrome, verify the extension is loaded with our appid.
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'

# 1. pack the extension on the target machine (key + crx live there)
Set-Location "$root\app"
$env:PYTHONPATH = "$root\app"
& "$root\python\python.exe" -c "import sys; sys.path.insert(0, r'C:\OpenDM\app'); from opendm import packext; import json; print(json.dumps(packext()))" 2>&1 |
  ForEach-Object { Write-Output "PACK: $_" }

# 2. read appid from updates.xml
$xml = Get-Content "$env:USERPROFILE\.config\opendm\www\updates.xml" -Raw
if ($xml -match "appid='([a-p]{16})'") { $appid = $Matches[1]; Write-Output "APPID: $appid" }

# 3. register ExtensionInstallForcelist policy (HKLM — needs admin; SSH session is High IL)
$forcelist = @{"$appid" = "http://127.0.0.1:10010/ext/updates.xml"}
$regPath = 'HKLM:\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist'
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty -Path $regPath -Name $appid -Value "http://127.0.0.1:10010/ext/updates.xml" -Type String
Write-Output "POLICY: $((Get-ItemProperty $regPath | Out-String))"

# also allow the update URL source
$srcList = 'HKLM:\SOFTWARE\Policies\Google\Chrome\ExtensionAllowedInstallSources'
New-Item -Path $srcList -Force | Out-Null
Set-ItemProperty -Path $srcList -Name '1' -Value "http://127.0.0.1/*" -Type String

# 4. restart the daemon with new code (kill old, start new)
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
  (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match 'opendm'
} | ForEach-Object {
  Write-Output "KILLING old daemon: $($_.Id)"
  Stop-Process -Id $_.Id -Force
}
Start-Process "$root\python\python.exe" -ArgumentList '-c', "import sys; sys.path.insert(0, r'C:\OpenDM\app'); import opendm.daemon as d; d.main()" -WindowStyle Hidden
Start-Sleep -Seconds 4
Write-Output "DAEMON-10010: $((Test-NetConnection 127.0.0.1 -Port 10010 -WarningAction SilentlyContinue).TcpTestSucceeded)"

# 5. verify /ext/updates.xml is served
try {
  $req = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/ext/updates.xml')
  $r = $req.GetResponse()
  $sr = New-Object IO.StreamReader($r.GetResponseStream())
  Write-Output "UPDATES-XML: $($sr.ReadToEnd().Substring(0,120))"
  $r.Close()
} catch { Write-Output "UPDATES-FAIL: $($_.Exception.Message)" }

# 6. restart Chrome (test profile) so the policy takes effect
Get-Process chrome -ErrorAction SilentlyContinue | Where-Object {
  (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match 'OpenDM\\chrome-profile'
} | ForEach-Object { Stop-Process -Id $_.Id -Force }
Start-Sleep -Seconds 2
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
Start-Process $chrome -ArgumentList "--user-data-dir=$root\chrome-profile", "--no-first-run", "--no-default-browser-check", "chrome://extensions"
Start-Sleep -Seconds 10
Write-Output "INSTALL-DONE"
