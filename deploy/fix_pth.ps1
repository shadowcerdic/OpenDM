# Fix python313._pth so embedded python always finds opendm regardless of cwd/shortcut
$pth = "C:\OpenDM\python\python313._pth"
$lines = Get-Content $pth
$hasApp = $false
foreach ($line in $lines) {
    if ($line.Trim() -eq "C:\OpenDM\app") { $hasApp = $true }
}
if (-not $hasApp) {
    Add-Content -Path $pth -Value "C:\OpenDM\app"
}
Write-Output "=== FIXED _PTH ==="
Get-Content $pth
