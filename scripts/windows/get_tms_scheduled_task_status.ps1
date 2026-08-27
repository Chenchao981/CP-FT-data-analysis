[CmdletBinding()]
param(
    [string]$TaskPath = '\NCE\TMS\',
    [string]$ApiUrl = 'http://127.0.0.1:8000/api/v1/health/ready',
    [switch]$ProbeApi,
    [switch]$RequireAll
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$taskNames = @('TMS-API', 'TMS-Worker', 'TMS-QuickCleanup')
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
        }
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $TaskPath
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

if ($RequireAll -and @($results | Where-Object { -not $_.Installed }).Count -gt 0) {
    exit 1
}
