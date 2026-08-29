[CmdletBinding()]
param(
    [PSCredential]$Credential,
    [string]$TaskPath = '\NCE\TMS\',
    [ValidateSet('DryRun', 'Delete')]
    [string]$CleanupMode = 'DryRun',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$CleanupAt = '02:00',
    [ValidateSet('DryRun', 'Delete')]
    [string]$FormalCleanupMode = 'DryRun',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$FormalCleanupAt = '03:00',
    [switch]$StartAfterInstall,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-TmsTaskPath {
    param([string]$Value)
    if ($Value -notmatch '^\\[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.-]+)*\\$') {
        throw 'TaskPath must use a bounded form such as \NCE\TMS\.'
    }
}

function Get-TmsActionArguments {
    param(
        [string]$ScriptPath,
        [string]$AdditionalArguments = ''
    )
    $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`""
    if (-not [string]::IsNullOrWhiteSpace($AdditionalArguments)) {
        $arguments = "$arguments $AdditionalArguments"
    }
    return $arguments
}

Assert-TmsTaskPath -Value $TaskPath
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$apiScript = Join-Path $PSScriptRoot 'run_tms_api.ps1'
$workerScript = Join-Path $PSScriptRoot 'run_tms_worker.ps1'
$cleanupScript = Join-Path $PSScriptRoot 'run_tms_cleanup.ps1'
$formalCleanupScript = Join-Path $PSScriptRoot 'run_tms_formal_cleanup.ps1'
$preflightScript = Join-Path $PSScriptRoot 'test_tms_production_preflight.ps1'
$requiredFiles = @(
    $powershellExe,
    $apiScript,
    $workerScript,
    $cleanupScript,
    $formalCleanupScript,
    $preflightScript,
    (Join-Path $workspace '.env.runtime.ps1'),
    (Join-Path $workspace '.conda-env\python.exe')
)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Missing deployment dependency: $requiredFile"
    }
}

$cleanupArguments = if ($CleanupMode -eq 'DryRun') { '-DryRun' } else { '' }
$formalCleanupArguments = if ($FormalCleanupMode -eq 'Delete') { '-Delete' } else { '' }
$actions = @{
    'TMS-API' = New-ScheduledTaskAction -Execute $powershellExe -Argument (Get-TmsActionArguments -ScriptPath $apiScript) -WorkingDirectory $workspace
    'TMS-Worker' = New-ScheduledTaskAction -Execute $powershellExe -Argument (Get-TmsActionArguments -ScriptPath $workerScript) -WorkingDirectory $workspace
    'TMS-QuickCleanup' = New-ScheduledTaskAction -Execute $powershellExe -Argument (Get-TmsActionArguments -ScriptPath $cleanupScript -AdditionalArguments $cleanupArguments) -WorkingDirectory $workspace
    'TMS-FormalCleanup' = New-ScheduledTaskAction -Execute $powershellExe -Argument (Get-TmsActionArguments -ScriptPath $formalCleanupScript -AdditionalArguments $formalCleanupArguments) -WorkingDirectory $workspace
}
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$cleanupTime = [datetime]::Today.Add([timespan]::ParseExact($CleanupAt, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
$cleanupTrigger = New-ScheduledTaskTrigger -Daily -At $cleanupTime
$formalCleanupTime = [datetime]::Today.Add([timespan]::ParseExact($FormalCleanupAt, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
$formalCleanupTrigger = New-ScheduledTaskTrigger -Daily -At $formalCleanupTime
$longRunningSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([timespan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 20 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$cleanupSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

if ($ValidateOnly) {
    @(
        [PSCustomObject]@{ TaskPath = $TaskPath; TaskName = 'TMS-API'; Trigger = 'AtStartup'; Restart = '20 x 1 minute'; MultipleInstances = 'IgnoreNew'; LogonType = 'Password'; RunLevel = 'Limited'; CleanupMode = $null },
        [PSCustomObject]@{ TaskPath = $TaskPath; TaskName = 'TMS-Worker'; Trigger = 'AtStartup'; Restart = '20 x 1 minute'; MultipleInstances = 'IgnoreNew'; LogonType = 'Password'; RunLevel = 'Limited'; CleanupMode = $null },
        [PSCustomObject]@{ TaskPath = $TaskPath; TaskName = 'TMS-QuickCleanup'; Trigger = "Daily $CleanupAt"; Restart = '3 x 5 minutes'; MultipleInstances = 'IgnoreNew'; LogonType = 'Password'; RunLevel = 'Limited'; CleanupMode = $CleanupMode }
        [PSCustomObject]@{ TaskPath = $TaskPath; TaskName = 'TMS-FormalCleanup'; Trigger = "Daily $FormalCleanupAt"; Restart = '3 x 5 minutes'; MultipleInstances = 'IgnoreNew'; LogonType = 'Password'; RunLevel = 'Limited'; CleanupMode = $FormalCleanupMode }
    )
    return
}

# Production registration is allowed only after the fail-closed runtime contract
# succeeds. ACLs are checked separately while logged on as the service account;
# this installer never impersonates it and never modifies ACLs.
& $preflightScript -RuntimeConfig (Join-Path $workspace '.env.runtime.ps1') -SkipAclCheck | Out-Null

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw 'Administrator privileges are required to install unattended TMS tasks.'
}
if ($null -eq $Credential) {
    $Credential = Get-Credential -Message 'Enter the service account that can access SQL Server and registered source roots.'
}
if ($null -eq $Credential) {
    throw 'A service account credential is required for unattended startup tasks.'
}

$userName = $Credential.UserName
$password = $Credential.GetNetworkCredential().Password
try {
    $principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Password -RunLevel Limited
    $definitions = @{
        'TMS-API' = New-ScheduledTask -Action $actions['TMS-API'] -Trigger $startupTrigger -Settings $longRunningSettings -Principal $principal -Description 'NCE TMS API process'
        'TMS-Worker' = New-ScheduledTask -Action $actions['TMS-Worker'] -Trigger $startupTrigger -Settings $longRunningSettings -Principal $principal -Description 'NCE TMS Route A queue Worker'
        'TMS-QuickCleanup' = New-ScheduledTask -Action $actions['TMS-QuickCleanup'] -Trigger $cleanupTrigger -Settings $cleanupSettings -Principal $principal -Description "NCE TMS Quick Artifact cleanup ($CleanupMode)"
        'TMS-FormalCleanup' = New-ScheduledTask -Action $actions['TMS-FormalCleanup'] -Trigger $formalCleanupTrigger -Settings $cleanupSettings -Principal $principal -Description "NCE TMS Formal Artifact cleanup ($FormalCleanupMode)"
    }
    foreach ($taskName in @('TMS-API', 'TMS-Worker', 'TMS-QuickCleanup', 'TMS-FormalCleanup')) {
        Register-ScheduledTask `
            -TaskName $taskName `
            -TaskPath $TaskPath `
            -InputObject $definitions[$taskName] `
            -User $userName `
            -Password $password `
            -Force | Out-Null
    }
} finally {
    $password = $null
}

if ($StartAfterInstall) {
    Start-ScheduledTask -TaskName 'TMS-API' -TaskPath $TaskPath
    Start-ScheduledTask -TaskName 'TMS-Worker' -TaskPath $TaskPath
}

& (Join-Path $PSScriptRoot 'get_tms_scheduled_task_status.ps1') `
    -TaskPath $TaskPath `
    -ExpectedUser $userName `
    -ExpectedCleanupMode $CleanupMode `
    -ExpectedFormalCleanupMode $FormalCleanupMode `
    -RequireAll
