[CmdletBinding()]
param(
    [ValidateRange(0.1, 3600)]
    [double]$PollSeconds = 2.0,
    [switch]$Once,
    [switch]$ValidateOnly
)

. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')
$context = Get-TmsRuntimeContext -Role 'worker'
$entryPoint = Join-Path $context.Workspace 'scripts\run_route_a_worker.py'
if ($ValidateOnly) {
    Write-TmsValidationResult -Context $context -EntryPoint $entryPoint
    return
}

Initialize-TmsRuntime -Context $context
$arguments = @($entryPoint, '--poll-seconds', $PollSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))
if ($Once) {
    $arguments += '--once'
}
Set-Location -LiteralPath $context.Workspace
& $context.Python @arguments
exit $LASTEXITCODE
