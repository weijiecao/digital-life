@echo off
REM Called by Windows Task Scheduler: run 72h L0 soak without a console.
REM pythonw.exe avoids the console-close (0xC000013A) exit that killed the first attempt.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
".venv\Scripts\pythonw.exe" "harness\soak.py" --interval 300 --hours 72 >> "logs\soak_task.log" 2>&1
