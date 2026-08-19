$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$ModelReady = $false
if (Test-Path $VenvPython) {
  & $VenvPython -c "from listen_app.core import available_local_models; raise SystemExit(0 if available_local_models() else 1)"
  $ModelReady = ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path $VenvPython) -or -not $ModelReady) {
  & (Join-Path $Root 'scripts\setup.ps1')
}

if (-not (Test-Path (Join-Path $Root 'node_modules\electron'))) {
  npm install
}

npm start
