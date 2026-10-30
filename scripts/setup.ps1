$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$taskPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& $taskPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
& (Join-Path $PSScriptRoot 'install_tasks.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'Setup complete. Open http://127.0.0.1:8878/'
