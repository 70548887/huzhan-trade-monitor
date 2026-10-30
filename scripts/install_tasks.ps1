$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$collectScript = Join-Path $PSScriptRoot 'collect.ps1'
$logonScript = Join-Path $PSScriptRoot 'logon.ps1'
$collectAction = New-ScheduledTaskAction -Execute $taskPowerShell -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $collectScript) -WorkingDirectory $projectRoot
$loginAction = New-ScheduledTaskAction -Execute $taskPowerShell -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $logonScript) -WorkingDirectory $projectRoot
$firstRun = (Get-Date).AddMinutes(1)
$repeatTrigger = New-ScheduledTaskTrigger -Once -At $firstRun -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
Register-ScheduledTask -TaskName 'HuzhanMonitorCollector' -Action $collectAction -Trigger $repeatTrigger -Settings $taskSettings -Principal $taskPrincipal -Description 'Collect Huzhan public transactions every 10 minutes.' -Force | Out-Null
Register-ScheduledTask -TaskName 'HuzhanMonitorLogon' -Action $loginAction -Trigger $loginTrigger -Settings $taskSettings -Principal $taskPrincipal -Description 'Start the local dashboard and collect once at logon.' -Force | Out-Null
Write-Output 'Installed HuzhanMonitorCollector and HuzhanMonitorLogon.'
Write-Output ('First scheduled collection: ' + $firstRun.ToString('yyyy-MM-dd HH:mm:ss'))
& $taskPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_server.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
