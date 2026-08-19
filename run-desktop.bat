@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-desktop.ps1"
if errorlevel 1 pause
endlocal
