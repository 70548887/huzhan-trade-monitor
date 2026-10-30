$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logPath = Join-Path $projectRoot 'logs'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python virtual environment is missing. Run setup.ps1 first.' }
New-Item -ItemType Directory -Force -Path $logPath | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $projectRoot
& $taskPython -m huzhan_monitor.collector 2>&1 | Out-File -LiteralPath (Join-Path $logPath 'task-output.log') -Encoding utf8 -Append
exit $LASTEXITCODE
