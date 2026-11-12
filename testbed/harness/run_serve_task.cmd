@echo off
REM 由计划任务调用：testbed AstrBot 实例（6201），日志追加
cd /d "%~dp0.."
".venv\Scripts\python.exe" "harness\serve.py" >> "logs\serve.log" 2>&1
