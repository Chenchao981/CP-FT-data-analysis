$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$runtimeConfig = Join-Path $workspace '.env.runtime.ps1'
if (-not (Test-Path -LiteralPath $runtimeConfig)) {
    throw 'Missing .env.runtime.ps1'
}
. $runtimeConfig
Set-Location -LiteralPath $workspace
& (Join-Path $workspace '.conda-env\python.exe') -m uvicorn app.main:app --host 127.0.0.1 --port 8000
