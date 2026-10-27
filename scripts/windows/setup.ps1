param(
    # Also install the test and lint tools.
    [switch]$Dev,
    # Run the test suite after installing.
    [switch]$Test
)

# One-time setup on a new Windows PC: Python check, virtual environment,
# dependencies, .env file and a start-up self-check. Safe to run again.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

Step 'Checking Python (3.11 or newer)'
$python = $null
foreach ($candidate in @(@('py', '-3'), @('python'))) {
    $exe = Get-Command $candidate[0] -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    $arguments = @($candidate | Select-Object -Skip 1)
    $version = & $exe.Source @arguments -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]'3.11') {
        $python = @($exe.Source) + $arguments
        Write-Host "Found Python $version"
        break
    }
}
if (-not $python) {
    throw ('Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ ' +
        '(tick "Add python.exe to PATH") or run: winget install Python.Python.3.12  - then run setup again.')
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Step 'Creating the virtual environment (.venv)'
    & $python[0] @($python | Select-Object -Skip 1) -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating .venv failed.' }
} else {
    Step 'Using the existing virtual environment (.venv)'
}

Step 'Installing dependencies (this can take a few minutes the first time)'
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
$package = if ($Dev -or $Test) { '.[dev]' } else { '.' }
& $venvPython -m pip install --disable-pip-version-check --quiet -e $package
if ($LASTEXITCODE -ne 0) { throw 'Installing dependencies failed. Check the internet connection or proxy settings.' }

if (-not (Test-Path -LiteralPath '.env')) {
    Step 'Creating .env from .env.example'
    Copy-Item '.env.example' '.env'
    Write-Host 'Add your GEMINI_API_KEY to .env (without it, AI checks and summaries are off and every item is marked for review).' -ForegroundColor Yellow
} else {
    Step 'Keeping the existing .env'
}

Step 'Self-check: loading the application'
& $venvPython -c "import app.main; from app.extraction import ad_ocr; print('App loads. Advert reading (OCR):', ad_ocr.ocr_engine() or 'OFF - tender adverts cannot be read')"
if ($LASTEXITCODE -ne 0) { throw 'The application did not load; see the error above.' }

if ($Test) {
    Step 'Running tests'
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Some tests failed.' }
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host 'Start the app:          start.cmd   (then open http://127.0.0.1:8000/monitor)'
Write-Host 'Check one edition:      .venv\Scripts\python.exe -m app.monitor.epaper_monitor --publication daily-news'
Write-Host 'Run online requests:    .venv\Scripts\python.exe -m app.monitor.request_worker'
