# Create OpenDM Desktop Shortcut
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = $ws.CreateShortcut("$desktop\OpenDM.lnk")
$lnk.TargetPath = "C:\OpenDM\python\pythonw.exe"
$lnk.Arguments = "-m opendm.gui"
$lnk.WorkingDirectory = "C:\OpenDM\app"
$lnk.Description = "OpenDM — Multi-connection Download Manager"
$lnk.Save()
Write-Output "DESKTOP-SHORTCUT: $(Test-Path "$desktop\OpenDM.lnk")"
