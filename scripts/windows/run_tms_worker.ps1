[CmdletBinding()]
param(
    [ValidateRange(0.1, 3600)]
    [double]$PollSeconds = 2.0,
    [ValidateRange(1, 3600)]
    [double]$RegistryHeartbeatSeconds = 15.0,
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
$context = Get-TmsRuntimeContext -Role 'worker'
$entryPoint = Join-Path $context.Workspace 'scripts\run_route_a_worker.py'
if ($ValidateOnly) {
    Write-TmsValidationResult -Context $context -EntryPoint $entryPoint
    return
}

Initialize-TmsRuntime -Context $context
$runtimeOptions = @{
    WorkerId = if ($PSBoundParameters.ContainsKey('WorkerId')) { $WorkerId } else { [Environment]::GetEnvironmentVariable('TMS_WORKER_ID') }
    ExpectedDatabase = if ($PSBoundParameters.ContainsKey('ExpectedDatabase')) { $ExpectedDatabase } else { [Environment]::GetEnvironmentVariable('TMS_EXPECTED_DATABASE') }
    ExpectedSchemaRevision = if ($PSBoundParameters.ContainsKey('ExpectedSchemaRevision')) { $ExpectedSchemaRevision } else { [Environment]::GetEnvironmentVariable('TMS_EXPECTED_SCHEMA_REVISION') }
    ExpectedDatabaseServer = if ($PSBoundParameters.ContainsKey('ExpectedDatabaseServer')) { $ExpectedDatabaseServer } else { [Environment]::GetEnvironmentVariable('TMS_EXPECTED_DATABASE_SERVER') }
    ReadyFile = if ($PSBoundParameters.ContainsKey('ReadyFile')) { $ReadyFile } else { [Environment]::GetEnvironmentVariable('TMS_WORKER_READY_FILE') }
    StopFile = if ($PSBoundParameters.ContainsKey('StopFile')) { $StopFile } else { [Environment]::GetEnvironmentVariable('TMS_WORKER_STOP_FILE') }
}
$arguments = @(
    $entryPoint,
    '--poll-seconds', $PollSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--registry-heartbeat-seconds', $RegistryHeartbeatSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
)
foreach ($option in @(
    @('WorkerId', '--worker-id'),
    @('ExpectedDatabase', '--expected-database'),
    @('ExpectedSchemaRevision', '--expected-schema-revision'),
    @('ExpectedDatabaseServer', '--expected-database-server'),
    @('ReadyFile', '--ready-file'),
    @('StopFile', '--stop-file')
)) {
    $value = [string]$runtimeOptions[$option[0]]
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
