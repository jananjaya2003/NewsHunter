param(
    [string[]]$Publication = @('daily-news', 'sunday-observer'),
    [switch]$OpenReport
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found at $pythonPath"
}

Push-Location $projectRoot
try {
    $failed = @()
    $resultUrls = @()
    foreach ($name in $Publication) {
        # Checks the latest edition; a saved complete result is reused, not re-downloaded.
        $output = & $pythonPath -m app.monitor.epaper_monitor --publication $name --log "data\epaper-monitor-$name.log"
        # Exit code 3: the check worked but uploading to the online site did not.
        if ($LASTEXITCODE -ne 0) {
            $failed += $name
        }
        if ($LASTEXITCODE -notin 0, 3) {
            continue
        }
        $resultUrls += $output | Where-Object { $_ -like 'http*' } | Select-Object -Last 1
    }
    if ($OpenReport -and $resultUrls) {
        # The results page needs the local web app; start it if it is not running.
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if (-not $listener) {
            Start-Process -FilePath (Get-Command powershell.exe).Source -WindowStyle Hidden -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'run_web_app.ps1')
            )
            Start-Sleep -Seconds 8
        }
        foreach ($url in $resultUrls) {
            Start-Process -FilePath $url
        }
    }
    if ($failed) {
        throw "The e-paper check or its upload failed for: $($failed -join ', '). See data\epaper-monitor-<name>.log."
    }
}
finally {
    Pop-Location
}
