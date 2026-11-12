@echo off
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYTHONPATH=%ROOT%\dev;%PYTHONPATH%"
cd /d "%ROOT%\dev"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ was not found in PATH.
  pause
  exit /b 1
)

python -m dlm_console --workspace "%ROOT%" %*
if errorlevel 1 pause