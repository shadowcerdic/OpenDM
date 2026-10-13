$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath(0)
$lnk = $ws.CreateShortcut("$desktop\OpenDM.lnk")
$lnk.TargetPath = 'C:\OpenDM\python\pythonw.exe'
$lnk.Arguments = '-m opendm.gui'
$lnk.WorkingDirectory = 'C:\OpenDM\app'
$lnk.Save()
Write-Output "LNK-CREATED: $(Test-Path "$desktop\OpenDM.lnk")"
