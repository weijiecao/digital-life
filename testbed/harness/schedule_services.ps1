param(
    [ValidateSet('register', 'start', 'stop', 'status', 'unregister')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$Testbed = Split-Path -Parent $PSScriptRoot
$Tasks = [ordered]@{
    'DLM-Testbed-Proxy' = 'harness\run_proxy_task.cmd'
    'DLM-Testbed-Serve' = 'harness\run_serve_task.cmd'
}

function Get-Task($name) {
    Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
}

switch ($Action) {
    'register' {
        foreach ($name in $Tasks.Keys) {
            $cmd = Join-Path $Testbed $Tasks[$name]
            if (-not (Test-Path $cmd)) { throw "missing $cmd" }
            $taskAction = New-ScheduledTaskAction -Execute $cmd -WorkingDirectory $Testbed
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew `
                -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero)
            Register-ScheduledTask -TaskName $name `
                -Description 'DigitalLife testbed service: keep AstrBot/proxy running' `
                -Action $taskAction -Trigger $trigger -Settings $settings -Force | Out-Null
            Write-Host "registered: $name"
        }
    }
    'start' {
        foreach ($name in $Tasks.Keys) { Start-ScheduledTask -TaskName $name; Write-Host "started: $name" }
    }
    'stop' {
        foreach ($name in $Tasks.Keys) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Write-Host "stopped: $name"
        }
        Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -like '*run_proxy_task.cmd*' -or $_.CommandLine -like '*run_serve_task.cmd*' -or $_.CommandLine -like '*mock_openai.py --port 9101*' -or $_.CommandLine -like '*harness\serve.py*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    'status' {
        foreach ($name in $Tasks.Keys) {
            $task = Get-Task $name
            if (-not $task) { Write-Host "not registered: $name"; continue }
            $info = Get-ScheduledTaskInfo -TaskName $name
            Write-Host ("{0}: {1}  last=0x{2:X}" -f $name, $task.State, $info.LastTaskResult)
        }
        Write-Host ("ports: 9101=" + ((Get-NetTCPConnection -LocalPort 9101 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count) +
            " 6201=" + ((Get-NetTCPConnection -LocalPort 6201 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count) +
            " 6201_est=" + ((Get-NetTCPConnection -LocalPort 6201 -State Established -ErrorAction SilentlyContinue | Measure-Object).Count))
    }
    'unregister' {
        foreach ($name in $Tasks.Keys) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "unregistered: $name"
        }
    }
}
