# Fix round: pack with correct symbol, then policy + verify
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'
Set-Location "$root\app"

# 1. pack (packext.pack function, not module)
& "$root\python\python.exe" -c "import sys; sys.path.insert(0, r'C:\OpenDM\app'); from opendm.packext import pack; import json; r = pack(); print('PACK:', json.dumps(r)); open(r'C:\OpenDM\appid.txt','w').write(r['appid'])" 2>&1 |
  ForEach-Object { Write-Output "PACK: $_" }

$appid = (Get-Content "$root\appid.txt" -ErrorAction SilentlyContinue).Trim()
Write-Output "APPID-READ: $appid"

if ($appid) {
  # 2. policy
  $regPath = 'HKLM:\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist'
  New-Item -Path $regPath -Force | Out-Null
  Set-ItemProperty -Path $regPath -Name $appid -Value "http://127.0.0.1:10010/ext/updates.xml" -Type String
  $srcList = 'HKLM:\SOFTWARE\Policies\Google\Chrome\ExtensionAllowedInstallSources'
  New-Item -Path $srcList -Force | Out-Null
  Set-ItemProperty -Path $srcList -Name '1' -Value "http://127.0.0.1/*" -Type String
  Write-Output "POLICY-SET: $appid"
}

# 3. start daemon
Start-Process "$root\python\python.exe" -ArgumentList '-c', "import sys; sys.path.insert(0, r'C:\OpenDM\app'); import opendm.daemon as d; d.main()" -WindowStyle Hidden
Start-Sleep -Seconds 4
netstat -ano | findstr ":10010" | ForEach-Object { Write-Output "NETSTAT: $_" }

# 4. verify /ext serving
try {
  $req = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/ext/updates.xml')
  $r = $req.GetResponse()
  $sr = New-Object IO.StreamReader($r.GetResponseStream())
  Write-Output "UPDATES-XML-OK: $($sr.ReadToEnd().Substring(0,100))"
  $r.Close()
} catch { Write-Output "UPDATES-FAIL: $($_.Exception.Message)" }
try {
  $req2 = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/ext/manifest.crx')
  $r2 = $req2.GetResponse()
  Write-Output "CRX-OK: $([int]$r2.StatusCode) len=$($r2.ContentLength)"
  $r2.Close()
} catch { Write-Output "CRX-FAIL: $($_.Exception.Message)" }

# 5. restart Chrome test profile
Get-Process chrome -ErrorAction SilentlyContinue | ForEach-Object {
  $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
  if ($cl -match 'chrome-profile') { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 2
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
Start-Process $chrome -ArgumentList "--user-data-dir=$root\chrome-profile", "--no-first-run", "--no-default-browser-check", "about:blank"
Start-Sleep -Seconds 12

# 6. verify extension installed: check profile Extensions dir for our appid
$extDir = "$root\chrome-profile\Default\Extensions"
if (Test-Path $extDir) {
  Get-ChildItem $extDir | ForEach-Object { Write-Output "INSTALLED-EXT: $($_.Name)" }
} else { Write-Output "NO-EXTENSIONS-DIR" }

# 7. verify Secure Preferences mention
$prefs = "$root\chrome-profile\Default\Secure Preferences"
if (Test-Path $prefs) {
  $raw = Get-Content $prefs -Raw
  if ($raw -match $appid) { Write-Output "PREFS-MATCH: $appid FOUND" } else { Write-Output "PREFS-NOMATCH" }
}
Write-Output "FIXROUND-DONE"
