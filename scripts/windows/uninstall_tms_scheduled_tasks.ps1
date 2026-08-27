[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$TaskPath = '\NCE\TMS\',
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Force) {
    $ConfirmPreference = 'None'
}
$taskNames = @('TMS-API', 'TMS-Worker', 'TMS-QuickCleanup')
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        continue
    }
    if ($PSCmdlet.ShouldProcess("$TaskPath$taskName", 'Stop and unregister scheduled task')) {
        Stop-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -TaskPath $TaskPath -Confirm:$false
    }
}
