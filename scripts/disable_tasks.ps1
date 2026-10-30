$ErrorActionPreference = 'Stop'
$taskNames = @('HuzhanMonitorCollector', 'HuzhanMonitorLogon')
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        Write-Output ("Disabled: " + $taskName)
    }
}
Write-Output 'Scheduled collection and logon startup are disabled. Existing data is preserved.'
