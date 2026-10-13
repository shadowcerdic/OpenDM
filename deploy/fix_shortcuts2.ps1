$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$startup = [Environment]::GetFolderPath('Startup')

$lnkDesk = $ws.CreateShortcut($desktop + '\OpenDM.lnk')
$lnkDesk.TargetPath = 'C:\OpenDM\python\pythonw.exe'
$lnkDesk.Arguments = '-m opendm.gui'
$lnkDesk.WorkingDirectory = 'C:\OpenDM\app'
$lnkDesk.Save()

$lnkStart = $ws.CreateShortcut($startup + '\OpenDM.lnk')
$lnkStart.TargetPath = 'C:\OpenDM\python\pythonw.exe'
$lnkStart.Arguments = '-m opendm.gui'
$lnkStart.WorkingDirectory = 'C:\OpenDM\app'
$lnkStart.Save()

Write-Output 'SHORTCUTS_RECREATED_OK'
