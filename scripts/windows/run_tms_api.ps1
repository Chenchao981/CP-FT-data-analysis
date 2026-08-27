[CmdletBinding()]
param(
    [string]$ListenAddress,
    [ValidateRange(1, 65535)]
    [int]$Port = 0,
    [switch]$ValidateOnly
)

. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')
$context = Get-TmsRuntimeContext -Role 'api'
$entryPoint = Join-Path $context.Backend 'app\main.py'
if ($ValidateOnly) {
    Write-TmsValidationResult -Context $context -EntryPoint $entryPoint
    return
}

Initialize-TmsRuntime -Context $context
if ([string]::IsNullOrWhiteSpace($ListenAddress)) {
    $ListenAddress = if ([string]::IsNullOrWhiteSpace($env:TMS_API_HOST)) {
        '127.0.0.1'
    } else {
        $env:TMS_API_HOST
    }
}
if ($Port -eq 0) {
    $Port = if ([string]::IsNullOrWhiteSpace($env:TMS_API_PORT)) {
        8000
    } else {
        [int]$env:TMS_API_PORT
    }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'TMS API port must be between 1 and 65535.'
}

Set-Location -LiteralPath $context.Backend
& $context.Python -m uvicorn app.main:app --host $ListenAddress --port $Port
exit $LASTEXITCODE
