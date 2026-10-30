$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$logonScript = Join-Path $PSScriptRoot 'logon.ps1'
$loginAction = New-ScheduledTaskAction -Execute $taskPowerShell -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $logonScript) -WorkingDirectory $projectRoot
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
Register-ScheduledTask -TaskName 'HuzhanMonitorLogon' -Action $loginAction -Trigger $loginTrigger -Settings $taskSettings -Principal $taskPrincipal -Description 'Start the local dashboard with built-in collection at logon.' -Force | Out-Null
Write-Output 'Installed HuzhanMonitorLogon. The web service collects every 10 minutes.'
& $taskPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_server.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$legacyTask = Get-ScheduledTask -TaskName 'HuzhanMonitorCollector' -ErrorAction SilentlyContinue
if ($legacyTask) {
    $runningHealth = Invoke-WebRequest -Uri 'http://127.0.0.1:8878/health' -UseBasicParsing -TimeoutSec 5
    if ($runningHealth.Headers['X-Huzhan-Auto-Collect'] -eq '1') {
        Disable-ScheduledTask -TaskName 'HuzhanMonitorCollector' | Out-Null
        Write-Output 'Disabled the legacy collection task; built-in collection is enabled.'
    } else {
        Write-Output 'The running server has not enabled built-in collection. Legacy task left unchanged; restart the updated server and rerun this installer.'
    }
}
