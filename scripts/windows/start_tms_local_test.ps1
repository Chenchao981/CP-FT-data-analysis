[CmdletBinding()]
param(
    [ValidateRange(0.1, 3600)]
    [double]$WorkerPollSeconds = 2.0,
    [ValidateRange(5, 300)]
    [int]$ReadyTimeoutSeconds = 60,
    [switch]$UseConfiguredAuthentication,
    [switch]$NoBrowser,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$runtimeConfig = Join-Path $workspace '.env.runtime.ps1'
$python = Join-Path $workspace '.conda-env\python.exe'
$backend = Join-Path $workspace 'backend'
$frontend = Join-Path $workspace 'frontend'
$workerEntryPoint = Join-Path $workspace 'scripts\run_route_a_worker.py'
$viteEntryPoint = Join-Path $frontend 'node_modules\vite\bin\vite.js'
$stateDirectory = Join-Path $workspace 'artifacts\runtime\local-test'
$statePath = Join-Path $stateDirectory 'processes.json'
$workerStopFile = Join-Path $stateDirectory 'worker.stop'
$workerReadyFile = Join-Path $stateDirectory 'worker.ready.json'
$apiUrl = 'http://127.0.0.1:8000/api/v1/health/ready'
$frontendUrl = 'http://127.0.0.1:5173/'
$expectedDatabase = 'TMS_G0_DEV'
$expectedSchemaRevision = 'sql2014_0024'

. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')
. (Join-Path $PSScriptRoot 'TmsLocalRuntime.Common.ps1')

function Get-TmsNodeExecutable {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { return $null }
    return $command.Source
}

function ConvertTo-TmsProcessArguments {
    param([string[]]$Values)
    return (($Values | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
    }) -join ' ')
}

function Get-TmsListenerProcessId {
    param([int]$Port)
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($listeners.Count -eq 0) { return $null }
    if ($listeners.Count -gt 1) {
        throw "Port $Port has more than one listener. Stop the conflicting services before starting TMS."
    }
    return [int]$listeners[0]
}

function Find-TmsWorkerProcessId {
    return Find-TmsLocalRoleProcessId -Role 'worker' -Workspace $workspace -Python $python -Node $node
}

function Test-TmsPriorProcessRecord {
    param([string]$Role, [int]$ProcessId)
    if ($null -eq $priorState) { return $false }
    $matches = @($priorState.processes | Where-Object {
        $_.role -eq $Role -and [int]$_.process_id -eq $ProcessId
    })
    if ($matches.Count -eq 1) {
        return Test-TmsLocalProcess -Record $matches[0] -Workspace $workspace -Python $python -Node $node
    }
    if (
        $matches.Count -eq 0 -and
        [string]$priorState.pending_role -eq $Role -and
        [string]$priorState.status -in @('STARTING', 'DEGRADED')
    ) {
        $candidate = New-TmsLocalProcessRecord -Role $Role -ProcessId $ProcessId -Adopted $true
        return Test-TmsLocalProcess -Record $candidate -Workspace $workspace -Python $python -Node $node
    }
    return $false
}

function Start-TmsManagedProcess {
    param(
        [ValidateSet('api', 'worker', 'frontend')][string]$Role,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$LogPrefix,
        [PSCustomObject]$State,
        [string]$StatePath,
        [System.Collections.Generic.List[object]]$StartedRecords,
        [switch]$ClearTmsEnvironment
    )
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $standardOutput = Join-Path $stateDirectory "$LogPrefix-$stamp.stdout.log"
    $standardError = Join-Path $stateDirectory "$LogPrefix-$stamp.stderr.log"
    $env:TMS_PROCESS_NAME = $Role
    $argumentLine = ConvertTo-TmsProcessArguments -Values $Arguments
    $startAction = {
        Start-Process `
            -FilePath $Executable `
            -ArgumentList $argumentLine `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $standardOutput `
            -RedirectStandardError $standardError `
            -WindowStyle Hidden `
            -PassThru
    }.GetNewClosure()
    $process = $null
    $record = $null
    try {
        $process = if ($ClearTmsEnvironment) {
            Invoke-WithoutTmsEnvironment -Action $startAction
        } else {
            & $startAction
        }
        $record = New-TmsLocalProcessRecord -Role $Role -ProcessId $process.Id
        $StartedRecords.Add($record)
        Set-TmsStateProcessRecord -State $State -Record $record
        $State.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $StatePath -State $State

        Start-Sleep -Milliseconds 800
        $process.Refresh()
        if ($process.HasExited) {
            throw "$Role failed to start. Review the local runtime stderr log: $standardError"
        }
        if (-not (Test-TmsLocalProcess -Record $record -Workspace $workspace -Python $python -Node $node)) {
            throw "$Role started with an unexpected process identity and was stopped."
        }
        return $record
    } catch {
        $startupFailure = $_
        $cleanupConfirmed = $true
        if ($null -ne $process) {
            try {
                $process.Refresh()
                if (-not $process.HasExited) {
                    $process.Kill()
                    $cleanupConfirmed = $process.WaitForExit(5000)
                }
            } catch {
                $cleanupConfirmed = $false
            }
        }
        if (-not $cleanupConfirmed) {
            if ($null -eq $record -and $null -ne $process) {
                try {
                    $record = New-TmsLocalProcessRecord -Role $Role -ProcessId $process.Id
                    $StartedRecords.Add($record)
                    Set-TmsStateProcessRecord -State $State -Record $record
                    $State.updated_at_utc = [DateTime]::UtcNow.ToString('o')
                    Write-TmsLocalState -StatePath $StatePath -State $State
                } catch { }
            }
            throw "$Role startup failed and PID $($process.Id) could not be confirmed stopped. No later role was started; investigate this exact PID before retrying. Original error: $($startupFailure.Exception.Message)"
        }
        throw $startupFailure
    }
}

function Wait-TmsApiReady {
    param([int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-RestMethod -Uri $apiUrl -Method Get -TimeoutSec 5
            if (
                $response.status -eq 'ready' -and
                $response.database -eq $expectedDatabase -and
                $response.schema_revision -eq $expectedSchemaRevision -and
                -not [string]::IsNullOrWhiteSpace([string]$response.database_server)
            ) { return $response }
            if ($response.status -eq 'ready') {
                throw "Local test database guard rejected database '$($response.database)' at schema '$($response.schema_revision)'."
            }
        } catch {
            if ($_.Exception.Message -like 'Local test database guard rejected*') { throw }
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for the guarded API ready response from $apiUrl"
}

function Wait-TmsWebEndpoint {
    param([string]$Uri, [int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) { return }
        } catch { Start-Sleep -Milliseconds 500 }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Uri"
}

function Wait-TmsWorkerReady {
    param(
        [PSCustomObject]$Record,
        [int]$TimeoutSeconds,
        [string]$ExpectedDatabase,
        [string]$ExpectedSchemaRevision,
        [string]$ExpectedDatabaseServer
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (-not (Test-TmsLocalProcess -Record $Record -Workspace $workspace -Python $python -Node $node)) {
            throw 'Route A Worker exited during startup. Review the local Worker stderr log.'
        }
        if (Test-Path -LiteralPath $workerReadyFile -PathType Leaf) {
            try {
                $ready = Read-TmsLocalJsonFile -Path $workerReadyFile
                if (
                    $ready.status -eq 'READY' -and
                    [int]$ready.pid -eq [int]$Record.process_id -and
                    [string]$ready.database -eq $ExpectedDatabase -and
                    [string]$ready.schema_revision -eq $ExpectedSchemaRevision -and
                    [string]$ready.database_server -eq $ExpectedDatabaseServer
                ) { return }
            } catch { }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Timed out waiting for the Route A Worker SQL readiness file.'
}

function Stop-TmsStartedProcess {
    param([PSCustomObject]$Record)
    if (-not (Test-TmsLocalProcess -Record $Record -Workspace $workspace -Python $python -Node $node)) {
        return $true
    }
    if ($Record.role -eq 'worker') {
        [DateTime]::UtcNow.ToString('o') | Set-Content -LiteralPath $workerStopFile -Encoding ASCII
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        do {
            Start-Sleep -Milliseconds 500
            $running = Test-TmsLocalProcess -Record $Record -Workspace $workspace -Python $python -Node $node
        } while ($running -and [DateTime]::UtcNow -lt $deadline)
        return -not $running
    }
    Stop-Process -Id ([int]$Record.process_id) -Force -ErrorAction Stop
    return $true
}

$node = Get-TmsNodeExecutable
$requiredFiles = @($runtimeConfig, $python, $workerEntryPoint, $viteEntryPoint)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Missing local test dependency: $requiredFile"
    }
}
if ([string]::IsNullOrWhiteSpace($node) -or -not (Test-Path -LiteralPath $node -PathType Leaf)) {
    throw 'node.exe is not available on PATH. Install the frontend runtime before starting TMS.'
}
[void](Read-TmsUtf8File -Path $runtimeConfig)
Import-TmsRuntimeConfig -Path $runtimeConfig
if ($env:TMS_JOB_REPOSITORY -ne 'sql') {
    throw 'Local TMS testing requires TMS_JOB_REPOSITORY=sql.'
}
$configuredDatabase = Assert-TmsLocalDatabaseGuard -Python $python -ExpectedDatabase $expectedDatabase
$env:TMS_ENV = 'development'
$authenticationContract = Resolve-TmsLocalAuthenticationContract `
    -UseConfiguredAuthentication:$UseConfiguredAuthentication
$authenticationMode = [string]$authenticationContract.Mode
$authRequired = [bool]$authenticationContract.AuthRequired
$env:PYTHONIOENCODING = 'utf-8'
if ([string]::IsNullOrWhiteSpace($env:TMS_LOG_DIR)) {
    $env:TMS_LOG_DIR = Join-Path $workspace 'data\logs'
} elseif (-not [IO.Path]::IsPathRooted($env:TMS_LOG_DIR)) {
    $env:TMS_LOG_DIR = [IO.Path]::GetFullPath((Join-Path $workspace $env:TMS_LOG_DIR))
}

if ($ValidateOnly) {
    [PSCustomObject]@{
        mode = 'LOCAL_TEST'
        workspace = $workspace
        python = $python
        node = $node
        api_url = $apiUrl
        frontend_url = $frontendUrl
        expected_database = $expectedDatabase
        configured_database = $configuredDatabase
        expected_schema_revision = $expectedSchemaRevision
        job_repository = $env:TMS_JOB_REPOSITORY
        authentication = $authenticationMode
        auth_required = $authRequired
        status = 'VALID'
    }
    return
}

$mutex = Get-TmsLocalMutex -Workspace $workspace
try {
    Set-TmsLocalPrivateDirectory -Path $stateDirectory
    New-Item -ItemType Directory -Path $env:TMS_LOG_DIR -Force | Out-Null

    $priorState = $null
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        $priorState = Read-TmsLocalJsonFile -Path $statePath
        if ([string]$priorState.workspace -ne $workspace) {
            throw 'The existing local test state belongs to a different workspace.'
        }
        foreach ($priorRecord in @($priorState.processes)) {
            if ($priorRecord.role -notin @('api', 'worker', 'frontend')) {
                throw "Unknown role '$($priorRecord.role)' in the existing local state."
            }
        }
        foreach ($role in @('api', 'worker', 'frontend')) {
            if (@($priorState.processes | Where-Object { $_.role -eq $role }).Count -gt 1) {
                throw "The existing local state contains duplicate records for role '$role'. Stop it before starting again."
            }
        }
        if (
            -not (@($priorState.PSObject.Properties.Name) -contains 'auth_required') -or
            [bool]$priorState.auth_required -ne $authRequired -or
            [string]$priorState.authentication -ne $authenticationMode -or
            [string]$priorState.expected_database -ne $expectedDatabase -or
            [string]$priorState.expected_schema_revision -ne $expectedSchemaRevision
        ) {
            throw 'The running local environment uses a different authentication or database guard. Stop it before switching modes.'
        }
    }

    # Reject every foreign listener/Worker before starting any new managed
    # process. The checks are repeated immediately before adoption/start below
    # to keep the normal race protection as well.
    foreach ($preflight in @(
        [PSCustomObject]@{ role = 'api'; process_id = Get-TmsListenerProcessId -Port 8000 },
        [PSCustomObject]@{ role = 'frontend'; process_id = Get-TmsListenerProcessId -Port 5173 },
        [PSCustomObject]@{ role = 'api'; process_id = Find-TmsLocalRoleProcessId -Role 'api' -Workspace $workspace -Python $python -Node $node },
        [PSCustomObject]@{ role = 'frontend'; process_id = Find-TmsLocalRoleProcessId -Role 'frontend' -Workspace $workspace -Python $python -Node $node },
        [PSCustomObject]@{ role = 'worker'; process_id = Find-TmsWorkerProcessId }
    )) {
        if (
            $null -ne $preflight.process_id -and
            -not (Test-TmsPriorProcessRecord -Role $preflight.role -ProcessId ([int]$preflight.process_id))
        ) {
            if ($preflight.role -eq 'worker') {
                throw "Worker process $($preflight.process_id) was not started by this local test launcher."
            }
            $port = if ($preflight.role -eq 'api') { 8000 } else { 5173 }
            throw "Port $port is occupied by process $($preflight.process_id), but it was not started by this local test launcher."
        }
    }

    $instanceId = if ($null -ne $priorState -and $priorState.instance_id) {
        [string]$priorState.instance_id
    } else { [Guid]::NewGuid().ToString() }
    $startedRecords = [System.Collections.Generic.List[object]]::new()
    $state = [PSCustomObject]@{
        schema_version = 2
        instance_id = $instanceId
        mode = 'LOCAL_TEST'
        status = 'STARTING'
        pending_role = $null
        workspace = $workspace
        started_at_utc = [DateTime]::UtcNow.ToString('o')
        updated_at_utc = [DateTime]::UtcNow.ToString('o')
        authentication = $authenticationMode
        auth_required = $authRequired
        expected_database = $expectedDatabase
        expected_schema_revision = $expectedSchemaRevision
        database_server = $null
        database_version = $null
        api_url = $apiUrl
        frontend_url = $frontendUrl
        processes = if ($null -ne $priorState) { @($priorState.processes) } else { @() }
    }
    $records = @($state.processes)
    Write-TmsLocalState -StatePath $statePath -State $state

    try {
        $state.pending_role = 'api'
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
        $apiProcessId = Get-TmsListenerProcessId -Port 8000
        if ($null -eq $apiProcessId) {
            $apiProcessId = Find-TmsLocalRoleProcessId -Role 'api' -Workspace $workspace -Python $python -Node $node
        }
        if ($null -eq $apiProcessId) {
            $apiRecord = Start-TmsManagedProcess -Role 'api' -Executable $python `
                -Arguments @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') `
                -WorkingDirectory $backend -LogPrefix 'api' -State $state -StatePath $statePath `
                -StartedRecords $startedRecords
        } else {
            if (-not (Test-TmsPriorProcessRecord -Role 'api' -ProcessId $apiProcessId)) {
                throw "Port 8000 is occupied by process $apiProcessId, but it was not started by this local test launcher."
            }
            $apiRecord = New-TmsLocalProcessRecord -Role 'api' -ProcessId $apiProcessId -Adopted $true
        }
        Set-TmsStateProcessRecord -State $state -Record $apiRecord
        $state.pending_role = $null
        $records = @($state.processes)
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
        $apiMetadata = Wait-TmsApiReady -TimeoutSeconds $ReadyTimeoutSeconds
        $state.database_server = [string]$apiMetadata.database_server
        $state.database_version = [string]$apiMetadata.database_version
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state

        $state.pending_role = 'frontend'
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
        $frontendProcessId = Get-TmsListenerProcessId -Port 5173
        if ($null -eq $frontendProcessId) {
            $frontendProcessId = Find-TmsLocalRoleProcessId -Role 'frontend' -Workspace $workspace -Python $python -Node $node
        }
        if ($null -eq $frontendProcessId) {
            $frontendRecord = Start-TmsManagedProcess -Role 'frontend' -Executable $node `
                -Arguments @($viteEntryPoint, '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
                -WorkingDirectory $frontend -LogPrefix 'frontend' -State $state -StatePath $statePath `
                -StartedRecords $startedRecords -ClearTmsEnvironment
        } else {
            if (-not (Test-TmsPriorProcessRecord -Role 'frontend' -ProcessId $frontendProcessId)) {
                throw "Port 5173 is occupied by process $frontendProcessId, but it was not started by this local test launcher."
            }
            $frontendRecord = New-TmsLocalProcessRecord -Role 'frontend' -ProcessId $frontendProcessId -Adopted $true
        }
        Set-TmsStateProcessRecord -State $state -Record $frontendRecord
        $state.pending_role = $null
        $records = @($state.processes)
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
        Wait-TmsWebEndpoint -Uri $frontendUrl -TimeoutSeconds $ReadyTimeoutSeconds

        $state.pending_role = 'worker'
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
        $workerProcessId = Find-TmsWorkerProcessId
        if ($null -eq $workerProcessId) {
            foreach ($controlFile in @($workerStopFile, $workerReadyFile)) {
                if (Test-Path -LiteralPath $controlFile -PathType Leaf) {
                    Remove-Item -LiteralPath $controlFile -Force
                }
            }
            $workerRecord = Start-TmsManagedProcess -Role 'worker' -Executable $python `
                -Arguments @(
                    $workerEntryPoint,
                    '--poll-seconds', $WorkerPollSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
                    '--worker-id', "local-test-$instanceId",
                    '--stop-file', $workerStopFile,
                    '--ready-file', $workerReadyFile,
                    '--expected-database', $expectedDatabase,
                    '--expected-schema-revision', $expectedSchemaRevision,
                    '--expected-database-server', ([string]$state.database_server)
                ) -WorkingDirectory $workspace -LogPrefix 'worker' -State $state `
                -StatePath $statePath -StartedRecords $startedRecords
        } else {
            if (Test-Path -LiteralPath $workerStopFile -PathType Leaf) {
                throw 'The running Worker is already stopping. Wait for it to finish, then start the environment again.'
            }
            if (-not (Test-TmsPriorProcessRecord -Role 'worker' -ProcessId $workerProcessId)) {
                throw "Worker process $workerProcessId was not started by this local test launcher."
            }
            $workerRecord = New-TmsLocalProcessRecord -Role 'worker' -ProcessId $workerProcessId -Adopted $true
        }
        Set-TmsStateProcessRecord -State $state -Record $workerRecord
        $state.pending_role = $null
        $records = @($state.processes)
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
        Wait-TmsWorkerReady -Record $workerRecord -TimeoutSeconds $ReadyTimeoutSeconds `
            -ExpectedDatabase $expectedDatabase `
            -ExpectedSchemaRevision $expectedSchemaRevision `
            -ExpectedDatabaseServer ([string]$state.database_server)

        $state.status = 'RUNNING'
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        Write-TmsLocalState -StatePath $statePath -State $state
    } catch {
        $startFailure = $_
        $state.status = 'DEGRADED'
        $state.updated_at_utc = [DateTime]::UtcNow.ToString('o')
        try { Write-TmsLocalState -StatePath $statePath -State $state } catch { }
        $rollback = @($startedRecords)
        [array]::Reverse($rollback)
        $workerStillRunning = $false
        foreach ($record in $rollback) {
            if ($workerStillRunning -and $record.role -ne 'worker') { continue }
            try {
                $stopped = Stop-TmsStartedProcess -Record $record
                if ($record.role -eq 'worker' -and -not $stopped) { $workerStillRunning = $true }
            } catch {
                if ($record.role -eq 'worker') { $workerStillRunning = $true }
            }
        }
        throw $startFailure
    }
} finally {
    Exit-TmsLocalMutex -Mutex $mutex
}

Write-Host ''
Write-Host 'TMS local test environment is ready.' -ForegroundColor Green
Write-Host "Frontend: $frontendUrl"
Write-Host "API ready: $apiUrl"
Write-Host "Database guard: $expectedDatabase / $expectedSchemaRevision"
Write-Host "Authentication: $authenticationMode"
@($records) | Select-Object role, process_id, adopted | Format-Table -AutoSize

if (-not $NoBrowser) {
    Invoke-WithoutTmsEnvironment -Action { Start-Process $frontendUrl | Out-Null }
}
