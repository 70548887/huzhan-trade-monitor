$ErrorActionPreference = 'Stop'
$taskNames = @('HuzhanMonitorCollector', 'HuzhanMonitorLogon')
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        Write-Output ("Disabled: " + $taskName)
    }
}
Write-Output 'Logon startup and legacy tasks are disabled. A running web service will keep collecting until stopped. Existing data is preserved.'
