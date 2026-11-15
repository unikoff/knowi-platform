$ErrorActionPreference = 'Stop'
$backupRoot = Join-Path $env:USERPROFILE '.local\share\knowi-backups'
$taskName = 'Knowi encrypted backup pull'
if ((Get-TimeZone).Id -ne 'Russian Standard Time') { throw 'Expected Moscow system timezone; review trigger timezone before registration.' }
$priorTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($priorTask) {
    if ($priorTask.Description -notlike 'Knowi verified encrypted backup pull*') { throw 'Existing task is not managed by this installer.' }
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $backupRoot 'state\task-before.xml') -Encoding Unicode
}
foreach ($filename in @('pull.py','common.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $filename) -Destination (Join-Path $backupRoot ('bin\' + $filename)) -Force
}
$action = New-ScheduledTaskAction -Execute (Join-Path $backupRoot 'runtime\Scripts\pythonw.exe') -Argument ('"' + (Join-Path $backupRoot 'bin\pull.py') + '"') -WorkingDirectory $backupRoot
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Thursday -At '12:00'
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$settings.StartWhenAvailable = $false
$settings.WakeToRun = $false
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Knowi verified encrypted backup pull: Monday and Thursday 12:00 Moscow; no startup/logon or missed-run catch-up.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force -ErrorAction Stop | Out-Null
Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $backupRoot 'state\task.xml') -Encoding Unicode
[xml]$registered = Export-ScheduledTask -TaskName $taskName
if ($registered.Task.Triggers.ChildNodes.Count -ne 1 -or $registered.Task.Triggers.ChildNodes[0].LocalName -ne 'CalendarTrigger') { throw 'Unexpected task trigger.' }
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object TaskName,NextRunTime,LastTaskResult
Start-ScheduledTask -TaskName $taskName
