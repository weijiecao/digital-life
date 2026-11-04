# Register/status/start/stop the L0 soak Windows scheduled task (current user).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action register
#   ... -Action status | start | stop | unregister
param(
    [ValidateSet('register', 'unregister', 'status', 'start', 'stop')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$TaskName = 'DLM-L0-Soak'
$Testbed = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $Testbed 'harness\run_soak_task.cmd'
$LogDir = Join-Path $Testbed 'logs'

switch ($Action) {
    'register' {
        if (-not (Test-Path $Runner)) { throw "missing runner: $Runner" }
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        $taskAction = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory $Testbed
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Hours 80)
        Register-ScheduledTask -TaskName $TaskName `
            -Description 'DigitalLife L0 soak: record 72h runtime metrics every 5 minutes' `
            -Action $taskAction -Trigger $trigger -Settings $settings -Force | Out-Null
        Write-Host "registered: $TaskName (at logon, hard stop after 80h)"
    }
    'unregister' {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "unregistered: $TaskName"
    }
    'status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) { Write-Host "not registered: $TaskName"; exit 1 }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "task: $TaskName"
        Write-Host "state: $($task.State)"
        Write-Host "last run: $($info.LastRunTime)  result: 0x$('{0:X}' -f $info.LastTaskResult)"
        Write-Host "next run: $($info.NextRunTime)"
        $jsonl = Join-Path $LogDir 'soak.jsonl'
        if (Test-Path $jsonl) {
            Write-Host "soak.jsonl rows: $((Get-Content $jsonl | Measure-Object).Count)"
        }
    }
    'start' {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "started: $TaskName"
    }
    'stop' {
        Stop-ScheduledTask -TaskName $TaskName
        Write-Host "stopped: $TaskName"
    }
}
