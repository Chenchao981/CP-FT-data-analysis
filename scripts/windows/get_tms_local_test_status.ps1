[CmdletBinding()]
param(
    [switch]$AsJson,
    [switch]$RequireReady
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $workspace '.conda-env\python.exe'
$stateDirectory = Join-Path $workspace 'artifacts\runtime\local-test'
$statePath = Join-Path $stateDirectory 'processes.json'
$workerStopFile = Join-Path $stateDirectory 'worker.stop'
$workerReadyFile = Join-Path $stateDirectory 'worker.ready.json'
$exportWorkerStopFile = Join-Path $stateDirectory 'export-worker.stop'
$exportWorkerReadyFile = Join-Path $stateDirectory 'export-worker.ready.json'
$apiUrl = 'http://127.0.0.1:8000/api/v1/health/ready'
$frontendUrl = 'http://127.0.0.1:5173/'
. (Join-Path $PSScriptRoot 'TmsLocalRuntime.Common.ps1')

$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
$node = if ($null -eq $nodeCommand) { '' } else { $nodeCommand.Source }

function Get-TmsListenerProcessId {
    param([int]$Port)
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($listeners.Count -eq 1) { return [int]$listeners[0] }
    return $null
}

function Test-TmsWebEndpoint {
    param([string]$Uri)
    foreach ($attempt in 1..3) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) { return $true }
        } catch {
            if ($attempt -lt 3) { Start-Sleep -Milliseconds 300 }
        }
    }
    return $false
}

function Get-TmsRoleRecord {
    param([PSCustomObject]$State, [string]$Role)
    $matches = @($State.processes | Where-Object { $_.role -eq $Role })
    if ($matches.Count -eq 1) { return $matches[0] }
    return $null
}

$statePresent = Test-Path -LiteralPath $statePath -PathType Leaf
$state = $null
$processes = @()
$apiReady = $false
$frontendReady = $false
$workerReady = $false
$exportWorkerReady = $false
$exportWorkerDraining = Test-Path -LiteralPath $exportWorkerStopFile -PathType Leaf
$workerDraining = Test-Path -LiteralPath $workerStopFile -PathType Leaf
$database = $null
$schemaRevision = $null
$apiDatabaseServer = $null
$exportWorkerDatabase = $null
$exportWorkerSchemaRevision = $null
$exportWorkerDatabaseServer = $null
$workerDatabase = $null
$workerSchemaRevision = $null
$workerDatabaseServer = $null
$authRequired = $null

if ($statePresent) {
    $state = Read-TmsLocalJsonFile -Path $statePath
    if ([string]$state.workspace -ne $workspace) {
        throw 'The local test state belongs to a different workspace.'
    }
    if (@($state.PSObject.Properties.Name) -contains 'auth_required') {
        $authRequired = [bool]$state.auth_required
    }
    $processes = @($state.processes | ForEach-Object {
        $running = if ([string]::IsNullOrWhiteSpace($node)) {
            $false
        } else {
            Test-TmsLocalProcess -Record $_ -Workspace $workspace -Python $python -Node $node
        }
        [PSCustomObject]@{
            role = $_.role
            process_id = $_.process_id
            running = $running
        }
    })

    $apiRecord = Get-TmsRoleRecord -State $state -Role 'api'
    $apiProcessReady = $null -ne $apiRecord -and @($processes | Where-Object { $_.role -eq 'api' -and $_.running }).Count -eq 1
    $apiListener = Get-TmsListenerProcessId -Port 8000
    if ($apiProcessReady -and $apiListener -eq [int]$apiRecord.process_id) {
        try {
            $ready = Invoke-RestMethod -Uri $apiUrl -Method Get -TimeoutSec 5
            $database = $ready.database
            $schemaRevision = $ready.schema_revision
            $apiDatabaseServer = $ready.database_server
            $apiReady = (
                $ready.status -eq 'ready' -and
                $ready.database -eq $state.expected_database -and
                $ready.schema_revision -eq $state.expected_schema_revision -and
                $apiDatabaseServer -eq [string]$state.database_server
            )
        } catch { $apiReady = $false }
    }

    $frontendRecord = Get-TmsRoleRecord -State $state -Role 'frontend'
    $frontendProcessReady = $null -ne $frontendRecord -and @($processes | Where-Object { $_.role -eq 'frontend' -and $_.running }).Count -eq 1
    $frontendListener = Get-TmsListenerProcessId -Port 5173
    if ($frontendProcessReady -and $frontendListener -eq [int]$frontendRecord.process_id) {
        $frontendReady = Test-TmsWebEndpoint -Uri $frontendUrl
    }

    $workerRecord = Get-TmsRoleRecord -State $state -Role 'worker'
    $workerProcessReady = $null -ne $workerRecord -and @($processes | Where-Object { $_.role -eq 'worker' -and $_.running }).Count -eq 1
    if ($workerProcessReady -and -not $workerDraining -and (Test-Path -LiteralPath $workerReadyFile -PathType Leaf)) {
        try {
            $workerMetadata = Read-TmsLocalJsonFile -Path $workerReadyFile
            $workerDatabase = [string]$workerMetadata.database
            $workerSchemaRevision = [string]$workerMetadata.schema_revision
            $workerDatabaseServer = [string]$workerMetadata.database_server
            $workerReady = (
                $workerMetadata.status -eq 'READY' -and
                [int]$workerMetadata.pid -eq [int]$workerRecord.process_id -and
                $workerDatabase -eq [string]$state.expected_database -and
                $workerSchemaRevision -eq [string]$state.expected_schema_revision -and
                $workerDatabaseServer -eq [string]$state.database_server
            )
        } catch { $workerReady = $false }
    }
    $exportWorkerRecord = Get-TmsRoleRecord -State $state -Role 'export-worker'
    $exportWorkerProcessReady = $null -ne $exportWorkerRecord -and @($processes | Where-Object { $_.role -eq 'export-worker' -and $_.running }).Count -eq 1
    if ($exportWorkerProcessReady -and -not $exportWorkerDraining -and (Test-Path -LiteralPath $exportWorkerReadyFile -PathType Leaf)) {
        try {
            $exportWorkerMetadata = Read-TmsLocalJsonFile -Path $exportWorkerReadyFile
            $exportWorkerDatabase = [string]$exportWorkerMetadata.database
            $exportWorkerSchemaRevision = [string]$exportWorkerMetadata.schema_revision
            $exportWorkerDatabaseServer = [string]$exportWorkerMetadata.database_server
            $exportWorkerReady = (
                $exportWorkerMetadata.status -eq 'READY' -and
                [int]$exportWorkerMetadata.pid -eq [int]$exportWorkerRecord.process_id -and
                $exportWorkerDatabase -eq [string]$state.expected_database -and
                $exportWorkerSchemaRevision -eq [string]$state.expected_schema_revision -and
                $exportWorkerDatabaseServer -eq [string]$state.database_server
            )
        } catch { $exportWorkerReady = $false }
    }
}

$allReady = (
    $statePresent -and
    [string]$state.status -eq 'RUNNING' -and
    $apiReady -and
    $frontendReady -and
    $workerReady -and
    $exportWorkerReady -and
    $null -ne $authRequired -and
    @($processes).Count -eq 4 -and
    @($processes | Where-Object { -not $_.running }).Count -eq 0
)
$result = [PSCustomObject]@{
    mode = 'LOCAL_TEST'
    state_present = $statePresent
    state_status = if ($null -eq $state) { 'MISSING' } else { [string]$state.status }
    all_ready = $allReady
    api_ready = $apiReady
    frontend_ready = $frontendReady
    worker_ready = $workerReady
    export_worker_ready = $exportWorkerReady
    export_worker_draining = $exportWorkerDraining
    export_worker_database = $exportWorkerDatabase
    export_worker_schema_revision = $exportWorkerSchemaRevision
    export_worker_database_server = $exportWorkerDatabaseServer
    worker_draining = $workerDraining
    auth_required = $authRequired
    database = $database
    schema_revision = $schemaRevision
    worker_database = $workerDatabase
    worker_schema_revision = $workerSchemaRevision
    database_server = $apiDatabaseServer
    worker_database_server = $workerDatabaseServer
    frontend_url = $frontendUrl
    processes = $processes
}

if ($AsJson) {
    $result | ConvertTo-Json -Depth 5
} else {
    $result | Select-Object mode, state_present, state_status, all_ready, api_ready, frontend_ready, worker_ready, export_worker_ready, worker_draining, export_worker_draining, auth_required, database, schema_revision, worker_database, worker_schema_revision, database_server, frontend_url | Format-List
    $processes | Format-Table -AutoSize
}
if ($RequireReady -and -not $allReady) { exit 1 }
