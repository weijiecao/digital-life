@echo off
REM 由计划任务调用：真实 DeepSeek 代理（9101），日志追加
cd /d "%~dp0.."
".venv\Scripts\python.exe" "harness\mock_openai.py" --port 9101 --log "logs\llm_requests.jsonl" --response-log "logs\llm_responses.jsonl" --upstream https://api.deepseek.com/v1 --key-file "instance\data\.deepseek_key" --model deepseek-chat >> "logs\mock_proxy.log" 2>&1
