param(
    [Parameter(Mandatory = $true)]
    [long]$PlayerId
)
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir
$Exe = Join-Path $env:LOCALAPPDATA 'bga-arknova-venv\Scripts\bga-arknova.exe'
if (-not (Test-Path $Exe)) { throw 'Run .\setup.ps1 first.' }
& $Exe scrape --player-id $PlayerId @args

