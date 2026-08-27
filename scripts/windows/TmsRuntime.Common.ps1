Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-TmsRuntimeContext {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Role
    )

    $workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    $runtimeConfig = Join-Path $workspace '.env.runtime.ps1'
    $python = Join-Path $workspace '.conda-env\python.exe'
    if (-not (Test-Path -LiteralPath $runtimeConfig -PathType Leaf)) {
        throw "Missing runtime configuration: $runtimeConfig"
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Missing TMS Python runtime: $python"
    }
    $configTokens = $null
    $configErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $runtimeConfig,
        [ref]$configTokens,
        [ref]$configErrors
    )
    if ($configErrors.Count -gt 0) {
        throw "Runtime configuration contains PowerShell syntax errors: $runtimeConfig"
    }

    return [PSCustomObject]@{
        Role = $Role
        Workspace = $workspace
        RuntimeConfig = $runtimeConfig
        Python = $python
        Backend = Join-Path $workspace 'backend'
        LogDir = Join-Path $workspace 'data\logs'
    }
}

function Initialize-TmsRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Context
    )

    . $Context.RuntimeConfig
    $env:TMS_PROCESS_NAME = $Context.Role
    if ([string]::IsNullOrWhiteSpace($env:TMS_LOG_DIR)) {
        $env:TMS_LOG_DIR = $Context.LogDir
    }
    if (-not [IO.Path]::IsPathRooted($env:TMS_LOG_DIR)) {
        $env:TMS_LOG_DIR = [IO.Path]::GetFullPath((Join-Path $Context.Workspace $env:TMS_LOG_DIR))
    }
    if ([string]::IsNullOrWhiteSpace($env:TMS_LOG_MAX_BYTES)) {
        $env:TMS_LOG_MAX_BYTES = '10485760'
    }
    if ([string]::IsNullOrWhiteSpace($env:TMS_LOG_BACKUP_COUNT)) {
        $env:TMS_LOG_BACKUP_COUNT = '10'
    }
    New-Item -ItemType Directory -Path $env:TMS_LOG_DIR -Force | Out-Null
}

function Write-TmsValidationResult {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Context,
        [Parameter(Mandatory = $true)]
        [string]$EntryPoint
    )

    if (-not (Test-Path -LiteralPath $EntryPoint -PathType Leaf)) {
        throw "Missing process entry point: $EntryPoint"
    }
    [PSCustomObject]@{
        Role = $Context.Role
        Workspace = $Context.Workspace
        Python = $Context.Python
        RuntimeConfigPresent = $true
        EntryPoint = $EntryPoint
        Status = 'VALID'
    }
}
