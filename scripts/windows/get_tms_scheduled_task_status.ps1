[CmdletBinding()]
param(
    [string]$TaskPath = '\NCE\TMS\',
    [string]$ApiUrl = 'http://127.0.0.1:8000/api/v1/health/ready',
    [string]$ExpectedUser,
    [ValidateSet('DryRun', 'Delete')]
    [string]$ExpectedCleanupMode = 'DryRun',
    [ValidateSet('DryRun', 'Delete')]
    [string]$ExpectedFormalCleanupMode = 'DryRun',
    [switch]$ProbeApi,
    [switch]$ProbeRuntime,
    [switch]$RequireAll
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($TaskPath -notmatch '^\\[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.-]+)*\\$') {
    throw 'TaskPath must use a bounded form such as \NCE\TMS\.'
}
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$expectedScripts = @{
    'TMS-API' = Join-Path $PSScriptRoot 'run_tms_api.ps1'
    'TMS-Worker' = Join-Path $PSScriptRoot 'run_tms_worker.ps1'
    'TMS-QuickCleanup' = Join-Path $PSScriptRoot 'run_tms_cleanup.ps1'
    'TMS-FormalCleanup' = Join-Path $PSScriptRoot 'run_tms_formal_cleanup.ps1'
}
$taskNames = @('TMS-API', 'TMS-Worker', 'TMS-QuickCleanup', 'TMS-FormalCleanup')
$results = foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        [PSCustomObject]@{
            TaskName = $taskName
            Installed = $false
            State = 'MISSING'
            LastRunTime = $null
            NextRunTime = $null
            LastTaskResult = $null
            DefinitionValid = $false
            DefinitionErrors = @('TASK_MISSING')
            CleanupMode = $null
        }
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $TaskPath
    $definitionErrors = New-Object 'System.Collections.Generic.List[string]'
    $cleanupMode = $null
    if (@($task.Actions).Count -ne 1) {
        $definitionErrors.Add('ACTION_COUNT')
    } else {
        $action = @($task.Actions)[0]
        if (-not ([string]$action.Execute).Equals($powershellExe, [StringComparison]::OrdinalIgnoreCase)) {
            $definitionErrors.Add('ACTION_EXECUTABLE')
        }
        if (-not ([string]$action.WorkingDirectory).Equals($workspace, [StringComparison]::OrdinalIgnoreCase)) {
            $definitionErrors.Add('WORKING_DIRECTORY')
        }
        $expectedScript = [string]$expectedScripts[$taskName]
        if ([string]$action.Arguments -notlike "*`"$expectedScript`"*") {
            $definitionErrors.Add('ACTION_SCRIPT')
        }
        if ([string]$action.Arguments -match '(?i)(password|\bpwd\b|jwt|bearer|token|secret)') {
            $definitionErrors.Add('SECRET_IN_ARGUMENTS')
        }
        if ($taskName -eq 'TMS-QuickCleanup') {
            $cleanupMode = if ([string]$action.Arguments -match '(?i)(?:^|\s)-DryRun(?:\s|$)') { 'DryRun' } else { 'Delete' }
            if ($cleanupMode -ne $ExpectedCleanupMode) {
                $definitionErrors.Add('CLEANUP_MODE')
            }
        } elseif ($taskName -eq 'TMS-FormalCleanup') {
            $cleanupMode = if ([string]$action.Arguments -match '(?i)(?:^|\s)-Delete(?:\s|$)') { 'Delete' } else { 'DryRun' }
            if ($cleanupMode -ne $ExpectedFormalCleanupMode) {
                $definitionErrors.Add('CLEANUP_MODE')
            }
        }
    }
    if ([string]$task.Principal.LogonType -ne 'Password') {
        $definitionErrors.Add('LOGON_TYPE')
    }
    if ([string]$task.Principal.RunLevel -ne 'Limited') {
        $definitionErrors.Add('RUN_LEVEL')
    }
    if (
        -not [string]::IsNullOrWhiteSpace($ExpectedUser) -and
        -not ([string]$task.Principal.UserId).Equals($ExpectedUser, [StringComparison]::OrdinalIgnoreCase)
    ) {
        $definitionErrors.Add('SERVICE_ACCOUNT')
    }
    if ([string]$task.Settings.MultipleInstances -ne 'IgnoreNew') {
        $definitionErrors.Add('MULTIPLE_INSTANCES')
    }
    $lastResultUnsigned = [BitConverter]::ToUInt32(
        [BitConverter]::GetBytes([int32]$info.LastTaskResult),
        0
    )
    [PSCustomObject]@{
        TaskName = $taskName
        Installed = $true
        State = [string]$task.State
        LastRunTime = $info.LastRunTime
        NextRunTime = $info.NextRunTime
        LastTaskResult = ('0x{0:X8}' -f $lastResultUnsigned)
        DefinitionValid = $definitionErrors.Count -eq 0
        DefinitionErrors = $definitionErrors.ToArray()
        CleanupMode = $cleanupMode
    }
}
$results

$logDir = Join-Path $workspace 'data\logs'
if (Test-Path -LiteralPath $logDir -PathType Container) {
    Get-ChildItem -LiteralPath $logDir -Filter '*.jsonl*' -File | Sort-Object Name | ForEach-Object {
        [PSCustomObject]@{
            LogFile = $_.Name
            Bytes = $_.Length
            LastWriteTime = $_.LastWriteTime
        }
    }
}

if ($ProbeApi) {
    try {
        $response = Invoke-RestMethod -Uri $ApiUrl -Method Get -TimeoutSec 10
        [PSCustomObject]@{ ApiUrl = $ApiUrl; Reachable = $true; Response = ($response | ConvertTo-Json -Compress) }
    } catch {
        [PSCustomObject]@{ ApiUrl = $ApiUrl; Reachable = $false; Response = $_.Exception.Message }
        if ($RequireAll) {
            exit 2
        }
    }
}

if ($ProbeRuntime) {
    & (Join-Path $PSScriptRoot 'test_tms_runtime_health.ps1') -ApiReadyUrl $ApiUrl
}

if (
    $RequireAll -and
    @($results | Where-Object { -not $_.Installed -or -not $_.DefinitionValid }).Count -gt 0
) {
    exit 1
}
