param(
    [string]$V1Db = "C:\AstrBot\data\plugin_data\astrbot_plugin_digital_memory_store\manual_test_memory.sqlite3",
    [string]$V15Db = "C:\AstrBot\data\plugin_data\astrbot_plugin_digital_memory_store\v1_5_events.sqlite3",
    [string]$ApiKeyFile = "",
    [switch]$Mock,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Dev = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Dev "..\testbed\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Python not found: $Python" }

$args = @("-m", "v1_5_consolidator.cli", "--v1-db", $V1Db, "--v15-db", $V15Db)
if ($Mock -or -not $ApiKeyFile) {
    $args += @("--provider", "mock")
} else {
    $args += @("--provider", "openai", "--allow-external", "--api-key-file", $ApiKeyFile)
}
$args += @("run-once")
if ($Force) { $args += "--force" }

Push-Location $Dev
try { & $Python @args } finally { Pop-Location }
