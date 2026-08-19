$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$WhisperConfig = Join-Path $Root 'models\whisper-small-int8\config.json'

if (-not (Test-Path $VenvPython) -or -not (Test-Path $WhisperConfig)) {
  & (Join-Path $Root 'scripts\setup.ps1')
}

if (-not (Test-Path (Join-Path $Root 'node_modules\electron'))) {
  npm install
}

npm start
