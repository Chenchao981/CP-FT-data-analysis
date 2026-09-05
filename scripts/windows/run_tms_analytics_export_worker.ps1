[CmdletBinding()]
param(
    [ValidateRange(0.1, 60)]
    [double]$PollSeconds = 2.0,
    [ValidateRange(30, 3600)]
    [int]$LeaseSeconds = 300,
    [ValidateRange(1, 300)]
    [int]$HeartbeatSeconds = 30,
    [string]$WorkerId,
    [string]$ExpectedDatabase,
    [string]$ExpectedSchemaRevision,
    [string]$ExpectedDatabaseServer,
    [string]$ReadyFile,
    [string]$StopFile,
    [switch]$Once,
    [switch]$ValidateOnly
)

. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')
$context = Get-TmsRuntimeContext -Role 'analytics-export-worker'
$entryPoint = Join-Path $context.Workspace 'scripts\run_analytics_export_worker.py'
if ($ValidateOnly) {
    Write-TmsValidationResult -Context $context -EntryPoint $entryPoint
    return
}

Initialize-TmsRuntime -Context $context
if ($HeartbeatSeconds -ge $LeaseSeconds) {
    throw 'HeartbeatSeconds must be below LeaseSeconds.'
}
$arguments = @(
    $entryPoint,
    '--poll-seconds', $PollSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--lease-seconds', $LeaseSeconds.ToString(),
    '--heartbeat-seconds', $HeartbeatSeconds.ToString()
)
$resolvedWorkerId = if ($PSBoundParameters.ContainsKey('WorkerId')) {
    $WorkerId
} else {
    [Environment]::GetEnvironmentVariable('TMS_ANALYTICS_EXPORT_WORKER_ID')
}
if (-not [string]::IsNullOrWhiteSpace($resolvedWorkerId)) {
    $arguments += @('--worker-id', $resolvedWorkerId)
}
foreach ($option in @(
    @('ExpectedDatabase', '--expected-database', 'TMS_EXPECTED_DATABASE'),
    @('ExpectedSchemaRevision', '--expected-schema-revision', 'TMS_EXPECTED_SCHEMA_REVISION'),
    @('ExpectedDatabaseServer', '--expected-database-server', 'TMS_EXPECTED_DATABASE_SERVER'),
    @('ReadyFile', '--ready-file', 'TMS_ANALYTICS_EXPORT_WORKER_READY_FILE'),
    @('StopFile', '--stop-file', 'TMS_ANALYTICS_EXPORT_WORKER_STOP_FILE')
)) {
    $value = if ($PSBoundParameters.ContainsKey($option[0])) {
        [string]$PSBoundParameters[$option[0]]
    } else { [Environment]::GetEnvironmentVariable($option[2]) }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $arguments += @($option[1], $value)
    }
}
if ($Once) {
    $arguments += '--once'
}
Set-Location -LiteralPath $context.Workspace
& $context.Python @arguments
exit $LASTEXITCODE
