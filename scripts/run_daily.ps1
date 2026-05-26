$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "$Stamp`_run_daily.log"

Set-Location $RepoRoot

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $Python) { $Python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $Python) {
    throw "Python not found. Install Python 3 and ensure `python` (or `python3`/`py`) is on PATH."
}

try {
    & $Python.Source ".\scripts\collect_ml_med.py" --commit --push 2>&1 | Tee-Object -FilePath $LogFile
    exit $LASTEXITCODE
}
catch {
    $_ | Out-File -FilePath $LogFile -Append -Encoding utf8
    throw
}
