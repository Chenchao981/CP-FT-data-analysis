Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-TmsUtf8File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
        return [IO.File]::ReadAllText($Path, $utf8)
    } catch {
        throw "Runtime configuration must be valid UTF-8: $Path"
    }
}

function Import-TmsRuntimeConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $content = Read-TmsUtf8File -Path $Path
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput(
        $content,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        throw "Runtime configuration contains PowerShell syntax errors: $Path"
    }
    $scriptBlock = [ScriptBlock]::Create($content)
    . $scriptBlock
}

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
    [void](Read-TmsUtf8File -Path $runtimeConfig)

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

    Import-TmsRuntimeConfig -Path $Context.RuntimeConfig
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
