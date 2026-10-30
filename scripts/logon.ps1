$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
& $taskPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_server.ps1')
& $taskPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'collect.ps1')
