$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logPath = Join-Path $projectRoot 'logs'
$dataPath = Join-Path $projectRoot 'data'
$healthUrl = 'http://127.0.0.1:8878/health'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python virtual environment is missing. Run setup.ps1 first.' }
New-Item -ItemType Directory -Force -Path $logPath, $dataPath | Out-Null
try {
    $existing = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($existing -eq 'huzhan-monitor-ok') {
        Write-Output 'Dashboard is already running.'
        exit 0
    }
    throw 'The dashboard port is occupied by another service.'
} catch {
    if ($_.Exception.Message -eq 'The dashboard port is occupied by another service.') { throw }
}
$portListener = Get-NetTCPConnection -LocalPort 8878 -State Listen -ErrorAction SilentlyContinue
if ($portListener) { throw 'Port 8878 is occupied by another service.' }
$env:PYTHONIOENCODING = 'utf-8'
$startParams = @{
    FilePath = $taskPython
    ArgumentList = '-m uvicorn huzhan_monitor.web:app --host 127.0.0.1 --port 8878'
    WorkingDirectory = $projectRoot
    WindowStyle = 'Hidden'
    PassThru = $true
    RedirectStandardOutput = (Join-Path $logPath 'server-out.log')
    RedirectStandardError = (Join-Path $logPath 'server-error.log')
}
$server = Start-Process @startParams
$server.Id | Set-Content -LiteralPath (Join-Path $dataPath 'server.pid') -Encoding ascii
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health -eq 'huzhan-monitor-ok') {
            Write-Output 'Dashboard ready: http://127.0.0.1:8878/'
            exit 0
        }
    } catch {}
}
throw 'Dashboard did not start. Check logs\server-error.log.'
