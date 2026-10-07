param(
    [string]$TaskName = 'Medical News Monitor Web App'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runnerPath = Join-Path $projectRoot 'run_web_app.ps1'
$powerShellPath = (Get-Command powershell.exe).Source
$taskArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runnerPath`""

$action = New-ScheduledTaskAction `
    -Execute $powerShellPath `
    -Argument $taskArguments `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn `
    -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Runs the local Medical News Monitor website after Windows sign-in.' `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
