$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$runtimeConfig = Join-Path $workspace '.env.runtime.ps1'
if (-not (Test-Path -LiteralPath $runtimeConfig)) {
    throw 'Missing .env.runtime.ps1'
}
. (Join-Path $PSScriptRoot 'windows\TmsRuntime.Common.ps1')
Import-TmsRuntimeConfig -Path $runtimeConfig
Set-Location -LiteralPath (Join-Path $workspace 'backend')
& (Join-Path $workspace '.conda-env\python.exe') -m uvicorn app.main:app --host 127.0.0.1 --port 8000
