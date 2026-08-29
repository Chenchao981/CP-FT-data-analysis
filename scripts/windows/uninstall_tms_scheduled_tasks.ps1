[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$TaskPath = '\NCE\TMS\',
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($TaskPath -notmatch '^\\[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.-]+)*\\$') {
    throw 'TaskPath must use a bounded form such as \NCE\TMS\.'
}
if ($Force) {
    $ConfirmPreference = 'None'
}
$taskNames = @('TMS-API', 'TMS-Worker', 'TMS-QuickCleanup', 'TMS-FormalCleanup')
$removedTaskNames = @()
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        continue
    }
    if ($PSCmdlet.ShouldProcess("$TaskPath$taskName", 'Stop and unregister scheduled task')) {
        Stop-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -Confirm:$false
        $removedTaskNames += $taskName
    }
}

if (-not $WhatIfPreference) {
    $remaining = @($taskNames | Where-Object {
        $null -ne (Get-ScheduledTask -TaskName $_ -TaskPath $TaskPath -ErrorAction SilentlyContinue)
    })
    if ($remaining.Count -gt 0) {
        throw "Scheduled task removal verification failed: $($remaining -join ', ')"
    }
    [PSCustomObject]@{
        TaskPath = $TaskPath
        RemovedTaskCount = $removedTaskNames.Count
        RemovedTaskNames = $removedTaskNames
        Status = 'VERIFIED_ABSENT'
    }
}
