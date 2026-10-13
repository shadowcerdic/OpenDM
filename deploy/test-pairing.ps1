# Restart daemon + test CRX serving + test pairing flow
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'

# 1. kill old
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
  $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
  if ($cl -match 'opendm') { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 1

# 2. start daemon via cmd hidden
Start-Process cmd -ArgumentList '/c', "C:\OpenDM\python\python.exe -c ""import sys; sys.path.insert(0, r'C:\OpenDM\app'); import opendm.daemon as d; d.main()"" > C:\OpenDM\daemon.log 2>&1" -WindowStyle Hidden
Start-Sleep -Seconds 3
Get-Content C:\OpenDM\daemon.log | Select-Object -First 5 | ForEach-Object { Write-Output "DLOG: $_" }
netstat -ano | findstr ":10010" | ForEach-Object { Write-Output "PORT: $_" }

# 3. test CRX and updates.xml serving
try {
  $r = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/ext/updates.xml').GetResponse()
  Write-Output "UPDATES-STATUS: $([int]$r.StatusCode) len=$($r.ContentLength)"
  $r.Close()
} catch { Write-Output "UPDATES-FAIL: $($_.Exception.Message)" }
try {
  $r2 = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/ext/manifest.crx').GetResponse()
  Write-Output "CRX-STATUS: $([int]$r2.StatusCode) len=$($r2.ContentLength)"
  $r2.Close()
} catch { Write-Output "CRX-FAIL: $($_.Exception.Message)" }

# 4. test pairing flow: activate window -> call /api/v1/pair -> verify token returned
$cfgDir = "$env:USERPROFILE\.config\opendm"
$pairFlag = "$cfgDir\pairing"
New-Item -ItemType File -Path $pairFlag -Force | Out-Null
Write-Output "PAIR-FLAG-CREATED: $(Test-Path $pairFlag)"

try {
  $req = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/api/v1/pair')
  # extension Origin
  $req.Headers.Add('Origin', 'chrome-extension://localtest')
  $resp = $req.GetResponse()
  $sr = New-Object IO.StreamReader($resp.GetResponseStream())
  $body = $sr.ReadToEnd()
  Write-Output "PAIR-RESPONSE: $body"
  $resp.Close()
  $tok = ($body | ConvertFrom-Json).token
  Write-Output "TOKEN-RECEIVED: $($tok.Length) chars ($($tok.Substring(0,8))...)"
} catch { Write-Output "PAIR-FAIL: $($_.Exception.Message)" }

# 5. verify token works on real status endpoint
try {
  $req2 = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/api/v1/status')
  $req2.Headers.Add('X-OpenDM-Token', $tok)
  $resp2 = $req2.GetResponse()
  $sr2 = New-Object IO.StreamReader($resp2.GetResponseStream())
  Write-Output "STATUS-WITH-PAIRED-TOKEN: $($sr2.ReadToEnd())"
  $resp2.Close()
} catch { Write-Output "STATUS-FAIL: $($_.Exception.Message)" }
Write-Output "E2E-PAIRING-COMPLETE"
