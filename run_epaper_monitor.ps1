param(
    [switch]$OpenReport
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found at $pythonPath"
}

Push-Location $projectRoot
try {
    & $pythonPath -m app.epaper_monitor
    if ($LASTEXITCODE -ne 0) {
        throw "The e-paper monitor failed with exit code $LASTEXITCODE."
    }
    if ($OpenReport) {
        $latestReport = Join-Path $projectRoot 'reports\latest.html'
        if (Test-Path -LiteralPath $latestReport) {
            Start-Process -FilePath $latestReport
        }
    }
}
finally {
    Pop-Location
}
