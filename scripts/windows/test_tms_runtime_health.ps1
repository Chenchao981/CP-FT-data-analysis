[CmdletBinding()]
param(
    [string]$RuntimeConfig,
    [string]$ApiReadyUrl,
    [ValidateRange(5, 86400)]
    [int]$StaleAfterSeconds = 90,
    [switch]$SkipWorkerRegistry
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')

function Assert-TmsLoopbackUri {
    param(
        [Parameter(Mandatory = $true)]
        [uri]$Uri
    )
    if ($Uri.Scheme -notin @('http', 'https')) {
        throw 'Health probe URL must use HTTP or HTTPS.'
    }
    if ($Uri.Host -notin @('127.0.0.1', 'localhost', '::1')) {
        throw 'Production health probes may only send credentials to a loopback endpoint.'
    }
}

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ([string]::IsNullOrWhiteSpace($RuntimeConfig)) {
    $RuntimeConfig = Join-Path $workspace '.env.runtime.ps1'
}
if (-not (Test-Path -LiteralPath $RuntimeConfig -PathType Leaf)) {
    throw "Runtime configuration does not exist: $RuntimeConfig"
}
$RuntimeConfig = (Resolve-Path -LiteralPath $RuntimeConfig).Path
Import-TmsRuntimeConfig -Path $RuntimeConfig
Assert-TmsRuntimeConfigContainsNoSecretLiterals -Path $RuntimeConfig
[void](Assert-TmsProductionRuntime -Workspace $workspace)

if ([string]::IsNullOrWhiteSpace($ApiReadyUrl)) {
    $port = [int]$env:TMS_API_PORT
    if ($port -lt 1 -or $port -gt 65535) {
        throw 'TMS_API_PORT must be between 1 and 65535.'
    }
    $ApiReadyUrl = "http://127.0.0.1:$port/api/v1/health/ready"
}
$readyUri = [uri]$ApiReadyUrl
Assert-TmsLoopbackUri -Uri $readyUri
$api = Invoke-RestMethod -Uri $readyUri -Method Get -TimeoutSec 10
foreach ($pair in @(
    @('status', 'ready'),
    @('database', [string]$env:TMS_EXPECTED_DATABASE),
    @('schema_revision', [string]$env:TMS_EXPECTED_SCHEMA_REVISION),
    @('database_server', [string]$env:TMS_EXPECTED_DATABASE_SERVER)
)) {
    $actual = [string]$api.($pair[0])
    if (-not $actual.Equals([string]$pair[1], [StringComparison]::OrdinalIgnoreCase)) {
        throw "API ready identity mismatch for $($pair[0])."
    }
}

$workerReadyPath = [IO.Path]::GetFullPath([string]$env:TMS_WORKER_READY_FILE)
if (-not (Test-Path -LiteralPath $workerReadyPath -PathType Leaf)) {
    throw "Worker ready file is missing: $workerReadyPath"
}
$readyItem = Get-Item -LiteralPath $workerReadyPath -Force
if (($readyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'Worker ready file must not be a symbolic link or reparse point.'
}
try {
    $workerReady = Get-Content -LiteralPath $workerReadyPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw 'Worker ready file is not valid JSON.'
}
foreach ($pair in @(
    @('status', 'READY'),
    @('worker_id', [string]$env:TMS_WORKER_ID),
    @('database', [string]$env:TMS_EXPECTED_DATABASE),
    @('schema_revision', [string]$env:TMS_EXPECTED_SCHEMA_REVISION),
    @('database_server', [string]$env:TMS_EXPECTED_DATABASE_SERVER)
)) {
    $actual = [string]$workerReady.($pair[0])
    if (-not $actual.Equals([string]$pair[1], [StringComparison]::OrdinalIgnoreCase)) {
        throw "Worker ready identity mismatch for $($pair[0])."
    }
}
$workerPid = 0
if (-not [int]::TryParse([string]$workerReady.pid, [ref]$workerPid) -or $workerPid -le 0) {
    throw 'Worker ready PID is invalid.'
}
if ($null -eq (Get-Process -Id $workerPid -ErrorAction SilentlyContinue)) {
    throw 'Worker ready PID is not running.'
}

$ftpReadyPath = [IO.Path]::GetFullPath([string]$env:TMS_FTP_WORKER_READY_FILE)
if (-not (Test-Path -LiteralPath $ftpReadyPath -PathType Leaf)) {
    throw 'FTP Worker ready file is missing.'
}
if (((Get-Item -LiteralPath $ftpReadyPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'FTP Worker ready file must not be a reparse point.'
}
try {
    $ftpReady = Get-Content -LiteralPath $ftpReadyPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw 'FTP Worker ready file is not valid JSON.'
}
foreach ($pair in @(
    @('status', 'READY'),
    @('worker_id', [string]$env:TMS_FTP_WORKER_ID),
    @('database', [string]$env:TMS_EXPECTED_DATABASE),
    @('schema_revision', [string]$env:TMS_EXPECTED_SCHEMA_REVISION),
    @('database_server', [string]$env:TMS_EXPECTED_DATABASE_SERVER)
)) {
    if (-not ([string]$ftpReady.($pair[0])).Equals([string]$pair[1], [StringComparison]::OrdinalIgnoreCase)) {
        throw "FTP Worker ready identity mismatch for $($pair[0])."
    }
}
$ftpReadyPid = 0
if (-not [int]::TryParse([string]$ftpReady.pid, [ref]$ftpReadyPid) -or $ftpReadyPid -le 0 -or
    $null -eq (Get-Process -Id $ftpReadyPid -ErrorAction SilentlyContinue)) {
    throw 'FTP Worker ready PID is not running.'
}

$registryChecked = $false
if (-not $SkipWorkerRegistry) {
    $token = [string]$env:TMS_HEALTH_BEARER_TOKEN
    if ($token.Length -lt 20) {
        throw 'TMS_HEALTH_BEARER_TOKEN must be injected for the authenticated Worker registry probe.'
    }
    $registryUri = [uri]::new($readyUri, "/api/v1/operations/workers?stale_after_seconds=$StaleAfterSeconds")
    Assert-TmsLoopbackUri -Uri $registryUri
    $fleet = Invoke-RestMethod -Uri $registryUri -Method Get -TimeoutSec 10 -Headers @{
        Authorization = "Bearer $token"
    }
    $worker = @($fleet.workers | Where-Object {
        ([string]$_.worker_id).Equals([string]$env:TMS_WORKER_ID, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($worker.Count -ne 1) {
        throw 'Expected Worker identity is missing or duplicated in the registry.'
    }
    if ([string]$worker[0].state -ne 'READY' -or [bool]$worker[0].is_stale) {
        throw 'Expected Worker is not READY with a current heartbeat.'
    }
    if (-not ([string]$worker[0].database_name).Equals([string]$env:TMS_EXPECTED_DATABASE, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Worker registry database identity mismatch.'
    }
    if (-not ([string]$worker[0].schema_revision).Equals([string]$env:TMS_EXPECTED_SCHEMA_REVISION, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Worker registry schema identity mismatch.'
    }
    $registryChecked = $true
    $token = $null
}

[PSCustomObject]@{
    Api = 'READY'
    Worker = 'READY'
    FtpWorker = 'READY'
    WorkerRegistryChecked = $registryChecked
    Database = [string]$env:TMS_EXPECTED_DATABASE
    DatabaseServer = [string]$env:TMS_EXPECTED_DATABASE_SERVER
    SchemaRevision = [string]$env:TMS_EXPECTED_SCHEMA_REVISION
    Status = 'VALID'
}
