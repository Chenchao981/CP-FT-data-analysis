[CmdletBinding()]
param(
    [ValidateRange(1, 10000)]
    [int]$Limit = 100,
    [switch]$Delete,
    [switch]$ValidateOnly
)

. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')
$context = Get-TmsRuntimeContext -Role 'formal-cleanup'
$entryPoint = Join-Path $context.Workspace 'scripts\run_formal_artifact_cleanup.py'
if ($ValidateOnly) {
    Write-TmsValidationResult -Context $context -EntryPoint $entryPoint
    return
}

Initialize-TmsRuntime -Context $context
$arguments = @($entryPoint, '--limit', $Limit.ToString())
if ($Delete) {
    $arguments += '--delete'
}
Set-Location -LiteralPath $context.Workspace
& $context.Python @arguments
exit $LASTEXITCODE
