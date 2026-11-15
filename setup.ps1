$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir
$Python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$VenvDir = Join-Path $env:LOCALAPPDATA 'bga-arknova-venv'
$AppExe = Join-Path $VenvDir 'Scripts\bga-arknova.exe'
if (Test-Path $AppExe) {
    Write-Host 'Already installed. Run .\dashboard.ps1, or .\scrape.ps1 -PlayerId YOUR_PLAYER_ID.'
    exit 0
}
if (-not (Test-Path $VenvDir)) { & $Python -m venv $VenvDir }
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
& $VenvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed' }
& $VenvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed' }
Write-Host 'Setup complete. Run .\dashboard.ps1, or .\scrape.ps1 -PlayerId YOUR_PLAYER_ID.'

