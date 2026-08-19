param(
  [ValidateSet('auto', 'tiny', 'small', 'medium')]
  [string]$WhisperSize = 'auto',
  [switch]$SkipOllama,
  [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }

$PythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PythonCommand) {
  $PythonExe = 'py'
  $PythonArgs = @('-3')
} else {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $PythonCommand) { throw 'Python 3 was not found. Install Python 3.11+ from https://www.python.org/downloads/windows/.' }
  $PythonExe = 'python'
  $PythonArgs = @()
}

$Venv = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
  Write-Step 'Creating the local Python environment'
  & $PythonExe @PythonArgs -m venv $Venv
}

Write-Step 'Installing local Python, microphone, VAD, and transcription dependencies'
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $Root 'requirements.txt') -r (Join-Path $Root 'requirements-audio.txt')

if ($WhisperSize -eq 'auto') {
  $WhisperSize = (& $VenvPython -c "from listen_app.hardware import detect_hardware; print(detect_hardware().whisper_model.replace('whisper-', '').replace('-int8', ''))").Trim()
}
Write-Step "Downloading and verifying Whisper $WhisperSize"
& $VenvPython -m listen_app.model_setup --size $WhisperSize --skip-ollama

if (-not $SkipOllama) {
  Write-Step 'Installing or locating Ollama and pulling the local Qwen2.5 note model'
  $OllamaModel = if ($env:LISTEN_OLLAMA_MODEL) { $env:LISTEN_OLLAMA_MODEL } else { (& $VenvPython -c "from listen_app.hardware import detect_hardware; print(detect_hardware().note_model)").Trim() }
  & $VenvPython -m listen_app.ollama_setup --windows --model $OllamaModel
} else {
  Write-Host 'Skipping Ollama; Listen will use its local heuristic note fallback.' -ForegroundColor Yellow
}

$env:LISTEN_DEFAULT_ASR_MODEL = 'auto'
Write-Step 'Checking local readiness'
& $VenvPython -m listen_app.preflight

Write-Host "`nSetup complete. Launch with .\run-desktop.ps1" -ForegroundColor Green
if ($Launch) {
  & (Join-Path $Root 'run-desktop.ps1')
}
