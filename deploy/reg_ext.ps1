# Register OpenDM extension via standard Windows registry external extension install
# Install extension via Chrome registry policy

$root = 'C:\OpenDM'
$crxPath = "$env:USERPROFILE\.config\opendm\www\manifest.crx"

# verify crx exists
if (-not (Test-Path $crxPath)) {
    Write-Output "CRX-MISSING: $crxPath"
    exit 1
}

$appid = (Get-Content "$root\appid.txt" -ErrorAction SilentlyContinue).Trim()
if (-not $appid) { $appid = "mfmpeokknhjkmmgj" }
$version = "1.2.0"

# Register in both HKCU and HKLM
$regHKCU = "HKCU:\Software\Google\Chrome\Extensions\$appid"
New-Item -Path $regHKCU -Force | Out-Null
Set-ItemProperty -Path $regHKCU -Name "path" -Value $crxPath -Type String
Set-ItemProperty -Path $regHKCU -Name "version" -Value $version -Type String

$regHKLM = "HKLM:\Software\Google\Chrome\Extensions\$appid"
New-Item -Path $regHKLM -Force | Out-Null
Set-ItemProperty -Path $regHKLM -Name "path" -Value $crxPath -Type String
Set-ItemProperty -Path $regHKLM -Name "version" -Value $version -Type String

Write-Output "REGISTERED_EXT_ID: $appid"
Write-Output "PATH: $crxPath"
