# connectivity diagnosis from the laptop
$ErrorActionPreference = 'Continue'
Write-Output "== DNS =="
try { (Resolve-DnsName speedtest.tele2.net -ErrorAction Stop | Select-Object -First 2 | ForEach-Object { $_.IPAddress }) } catch { "DNS-FAIL: $($_.Exception.Message)" }
Write-Output "== raw TCP 443 to github =="
$t = Test-NetConnection github.com -Port 443 -WarningAction SilentlyContinue
Write-Output "TCP443-github: $($t.TcpTestSucceeded)"
Write-Output "== raw TCP 80 to tele2 =="
$t2 = Test-NetConnection speedtest.tele2.net -Port 80 -WarningAction SilentlyContinue
Write-Output "TCP80-tele2: $($t2.TcpTestSucceeded)"
Write-Output "== HTTPS GET via .NET =="
try {
  $req = [Net.HttpWebRequest]::Create('https://proof.ovh.net/files/1Mb.dat')
  $req.Timeout = 12000
  $req.AddRange(0, 99)
  $resp = $req.GetResponse()
  Write-Output "OVH-HTTP: $([int]$resp.StatusCode) len=$($resp.ContentLength)"
  $resp.Close()
} catch { Write-Output "OVH-FAIL: $($_.Exception.Message)" }
Write-Output "== HTTP GET via .NET (tele2) =="
try {
  $req2 = [Net.HttpWebRequest]::Create('http://speedtest.tele2.net/1MB.zip')
  $req2.Timeout = 12000
  $req2.AddRange(0, 99)
  $resp2 = $req2.GetResponse()
  Write-Output "TELE2-HTTP: $([int]$resp2.StatusCode) len=$($resp2.ContentLength)"
  $resp2.Close()
} catch { Write-Output "TELE2-FAIL: $($_.Exception.Message)" }
Write-Output "== proxy settings =="
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' |
  Select-Object ProxyEnable, ProxyServer, AutoConfigURL | Format-List | Out-String
