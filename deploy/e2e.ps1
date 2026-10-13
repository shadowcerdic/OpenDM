# Final end-to-end: token-authenticated API call from Windows + queue a real download
$ErrorActionPreference = 'Continue'
$root = 'C:\OpenDM'

# read token from user config
$cfgFile = "$env:USERPROFILE\.config\opendm\daemon.json"
if (-not (Test-Path $cfgFile)) { $cfgFile = "$env:APPDATA\opendm\daemon.json" }
Write-Output "CFG: $cfgFile exists=$(Test-Path $cfgFile)"
$cfg = Get-Content $cfgFile -Raw | ConvertFrom-Json
$tok = $cfg.token

# status check
$req = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/api/v1/status')
$req.Headers.Add('X-OpenDM-Token', $tok)
try { $r = $req.GetResponse(); Write-Output "STATUS: $([int]$r.StatusCode)"; $r.Close() }
catch { Write-Output "STATUS-FAIL: $($_.Exception.Message)" }

# queue a real download through the API (like the extension does)
$body = '{"url":"https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-pc-windows-msvc.zip","connections":4,"headers":{"User-Agent":"OpenDM-E2E-Test"}}'
$req2 = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/api/v1/download')
$req2.Method = 'POST'
$req2.ContentType = 'application/json'
$req2.Headers.Add('X-OpenDM-Token', $tok)
$bytes = [Text.Encoding]::UTF8.GetBytes($body)
$req2.ContentLength = $bytes.Length
$s = $req2.GetRequestStream(); $s.Write($bytes, 0, $bytes.Length); $s.Close()
try {
  $r2 = $req2.GetResponse()
  $sr = New-Object IO.StreamReader($r2.GetResponseStream())
  Write-Output "DOWNLOAD-QUEUED: $($sr.ReadToEnd())"
  $r2.Close()
} catch { Write-Output "DL-FAIL: $($_.Exception.Message)" }

Start-Sleep -Seconds 12
# check job status
$req3 = [Net.HttpWebRequest]::Create('http://127.0.0.1:10010/api/v1/jobs')
$req3.Headers.Add('X-OpenDM-Token', $tok)
$r3 = $req3.GetResponse()
$sr3 = New-Object IO.StreamReader($r3.GetResponseStream())
Write-Output "JOBS: $($sr3.ReadToEnd().Substring(0, 400))"
$r3.Close()

# check the file landed in Downloads
$f = Get-ChildItem "$env:USERPROFILE\Downloads\ripgrep*" -ErrorAction SilentlyContinue | Select-Object -First 2
foreach ($x in $f) { Write-Output "FILE: $($x.Name) $($x.Length) bytes" }
Write-Output "E2E-DONE"
