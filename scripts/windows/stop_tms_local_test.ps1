[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(5, 600)]
    [int]$WorkerDrainTimeoutSeconds = 60
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
. (Join-Path $PSScriptRoot 'TmsLocalRuntime.Common.ps1')

$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    throw 'node.exe is not available on PATH; process identities cannot be verified safely.'
}
$node = $nodeCommand.Source

function Get-TmsListenerProcessId {
    param([int]$Port)
    $listener = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($listener.Count -eq 1) { return [int]$listener[0] }
    return $null
}

function Find-TmsWorkerProcessId {
    return Find-TmsLocalRoleProcessId -Role 'worker' -Workspace $workspace -Python $python -Node $node
}

function Get-TmsRecordedRole {
    param(
        [PSCustomObject]$State,
        [ValidateSet('api', 'worker', 'export-worker', 'frontend')]
        [string]$Role
    )
    $matches = @($State.processes | Where-Object { $_.role -eq $Role })
    if ($matches.Count -gt 1) {
        throw "Local state contains duplicate records for role '$Role'. No process was stopped."
    }
    if ($matches.Count -eq 1) {
        if (Test-TmsLocalProcess -Record $matches[0] -Workspace $workspace -Python $python -Node $node) {
            return $matches[0]
        }
        if ([string]$State.pending_role -ne $Role) { return $matches[0] }
    } elseif ([string]$State.pending_role -ne $Role) {
        return $null
    }
    $processId = switch ($Role) {
        'api' { Find-TmsLocalRoleProcessId -Role 'api' -Workspace $workspace -Python $python -Node $node }
        'frontend' { Find-TmsLocalRoleProcessId -Role 'frontend' -Workspace $workspace -Python $python -Node $node }
        'worker' { Find-TmsWorkerProcessId }
        'export-worker' { Find-TmsLocalRoleProcessId -Role 'export-worker' -Workspace $workspace -Python $python -Node $node }
    }
    if ($null -eq $processId) { return $null }
    $candidate = New-TmsLocalProcessRecord -Role $Role -ProcessId $processId -Adopted $true
    if (Test-TmsLocalProcess -Record $candidate -Workspace $workspace -Python $python -Node $node) {
        return $candidate
    }
    return $null
}

if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    Write-Host 'No managed TMS local test environment is recorded.' -ForegroundColor Yellow
    return
}

$mutex = Get-TmsLocalMutex -Workspace $workspace
try {
    if (-not $WhatIfPreference) {
        Set-TmsLocalPrivateDirectory -Path $stateDirectory
    }
    $state = Read-TmsLocalJsonFile -Path $statePath
    if ([string]$state.workspace -ne $workspace) {
        throw 'The local test state belongs to a different workspace. No process was stopped.'
    }
    foreach ($record in @($state.processes)) {
        if ($record.role -notin @('api', 'worker', 'export-worker', 'frontend')) {
            throw "Unknown role '$($record.role)' in local state. No process was stopped."
        }
    }

    $frontendRecord = Get-TmsRecordedRole -State $state -Role 'frontend'
    if ($null -eq $frontendRecord -or -not (Test-TmsLocalProcess -Record $frontendRecord -Workspace $workspace -Python $python -Node $node)) {
        Write-Host 'frontend: already stopped'
    } elseif ($PSCmdlet.ShouldProcess("PID $($frontendRecord.process_id) (frontend)", 'Stop managed TMS frontend')) {
        Stop-Process -Id ([int]$frontendRecord.process_id) -Force -ErrorAction Stop
        Write-Host 'frontend: stopped'
    }

    $workerRecord = Get-TmsRecordedRole -State $state -Role 'export-worker'
    $workerStopped = $true
    if ($null -eq $workerRecord -or -not (Test-TmsLocalProcess -Record $workerRecord -Workspace $workspace -Python $python -Node $node)) {
        Write-Host 'export-worker: already stopped'
    } elseif ($PSCmdlet.ShouldProcess("PID $($workerRecord.process_id) (worker)", 'Request managed TMS Worker graceful stop')) {
        [DateTime]::UtcNow.ToString('o') | Set-Content -LiteralPath $exportWorkerStopFile -Encoding ASCII
        $deadline = [DateTime]::UtcNow.AddSeconds($WorkerDrainTimeoutSeconds)
        do {
            Start-Sleep -Milliseconds 500
            $running = Test-TmsLocalProcess -Record $workerRecord -Workspace $workspace -Python $python -Node $node
        } while ($running -and [DateTime]::UtcNow -lt $deadline)
        if ($running) {
            $workerStopped = $false
            Write-Warning 'export-worker: still finishing its current run; it was not force-stopped'
        } else {
            Write-Host 'export-worker: stopped gracefully'
        }
    }

    if (-not $workerStopped) {
        throw 'Export Worker has not stopped yet. API and state were retained; run stop again after the current run finishes.'
    }

    $workerRecord = Get-TmsRecordedRole -State $state -Role 'worker'
    $workerStopped = $true
    if ($null -eq $workerRecord -or -not (Test-TmsLocalProcess -Record $workerRecord -Workspace $workspace -Python $python -Node $node)) {
        Write-Host 'worker: already stopped'
    } elseif ($PSCmdlet.ShouldProcess("PID $($workerRecord.process_id) (worker)", 'Request managed TMS Worker graceful stop')) {
        [DateTime]::UtcNow.ToString('o') | Set-Content -LiteralPath $workerStopFile -Encoding ASCII
        $deadline = [DateTime]::UtcNow.AddSeconds($WorkerDrainTimeoutSeconds)
        do {
            Start-Sleep -Milliseconds 500
            $running = Test-TmsLocalProcess -Record $workerRecord -Workspace $workspace -Python $python -Node $node
        } while ($running -and [DateTime]::UtcNow -lt $deadline)
        if ($running) {
            $workerStopped = $false
            Write-Warning 'worker: still finishing its current run; it was not force-stopped'
        } else {
            Write-Host 'worker: stopped gracefully'
        }
    }

    if (-not $workerStopped) {
        throw 'Worker has not stopped yet. API and state were retained; run stop again after the current run finishes.'
    }

    $apiRecord = Get-TmsRecordedRole -State $state -Role 'api'
    if ($null -eq $apiRecord -or -not (Test-TmsLocalProcess -Record $apiRecord -Workspace $workspace -Python $python -Node $node)) {
        Write-Host 'api: already stopped'
    } elseif ($PSCmdlet.ShouldProcess("PID $($apiRecord.process_id) (api)", 'Stop managed TMS API')) {
        Stop-Process -Id ([int]$apiRecord.process_id) -Force -ErrorAction Stop
        Write-Host 'api: stopped'
    }

    foreach ($controlFile in @($workerStopFile, $workerReadyFile, $exportWorkerStopFile, $exportWorkerReadyFile)) {
        if (
            (Test-Path -LiteralPath $controlFile -PathType Leaf) -and
            $PSCmdlet.ShouldProcess($controlFile, 'Remove local Worker control file')
        ) {
            Remove-Item -LiteralPath $controlFile -Force
        }
    }
    if ($PSCmdlet.ShouldProcess($statePath, 'Remove local test process state')) {
        Remove-Item -LiteralPath $statePath -Force
    }
} finally {
    Exit-TmsLocalMutex -Mutex $mutex
}

if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    Write-Host 'Stop preview completed; the environment remains recorded.' -ForegroundColor Yellow
} else {
    Write-Host 'TMS local test environment is stopped.' -ForegroundColor Green
}
