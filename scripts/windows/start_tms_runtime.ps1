[CmdletBinding()]
param(
    [ValidateSet('API', 'Worker', 'AnalyticsExportWorker', 'FtpCollectionWorker', 'AnalyticsExportCleanup', 'QuickCleanup', 'FormalCleanup')]
    [string]$Role = 'API',
    [string]$RuntimeHome,
    [string]$RuntimeConfigPath,
    [string]$PythonPath,
    [string]$ListenAddress,
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [switch]$Delete,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-TmsFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        $hash = $algorithm.ComputeHash($stream)
        return ([BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Test-TmsReleaseManifest {
    param([Parameter(Mandatory = $true)][string]$Workspace)
    $manifestPath = Join-Path $Workspace 'release-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'release-manifest.json is missing.'
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw 'release-manifest.json is invalid.'
    }
    if ([string]$manifest.format -ne 'NCE_TMS_RELEASE_V1') {
        throw 'Release manifest format is unsupported.'
    }
    if ([string]$manifest.schema_revision -notmatch '^sql2014_[0-9]{4}$') {
        throw 'Release manifest schema revision is invalid.'
    }
    $workspacePrefix = $Workspace.TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
    $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in @($manifest.files)) {
        $relative = [string]$entry.path
        if (
            [string]::IsNullOrWhiteSpace($relative) -or
            $relative.Contains('\') -or
            $relative -match '(^/|(^|/)\.\.?(?:/|$)|:)' -or
            $relative -match '(?i)(^|/)(?:data|raw|workspace|work|quarantine|logs|\.remember|secrets|credentials)(?:/|$)' -or
            $relative -match '(?i)(^|/)\.env(?:\.|$)'
        ) {
            throw "Release manifest contains a forbidden path: $relative"
        }
        $nativeRelative = $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
        $full = [IO.Path]::GetFullPath((Join-Path $Workspace $nativeRelative))
        if (-not $full.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Release file escapes the workspace: $relative"
        }
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            throw "Release file is missing: $relative"
        }
        $item = Get-Item -LiteralPath $full -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release file is a symbolic link or reparse point: $relative"
        }
        $actualHash = Get-TmsFileSha256 -Path $full
        if ($actualHash -ne ([string]$entry.sha256).ToLowerInvariant()) {
            throw "Release file hash mismatch: $relative"
        }
        if ([int64]$item.Length -ne [int64]$entry.size) {
            throw "Release file size mismatch: $relative"
        }
        if (-not $expected.Add($relative)) {
            throw "Release manifest contains a duplicate path: $relative"
        }
    }
    $actual = @(Get-ChildItem -LiteralPath $Workspace -Recurse -File -Force | ForEach-Object {
        if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release contains a symbolic link or reparse point: $($_.FullName)"
        }
        $relative = $_.FullName.Substring($workspacePrefix.Length).Replace('\', '/')
        if ($relative -ne 'release-manifest.json') {
            $relative
        }
    })
    $unexpected = @($actual | Where-Object { -not $expected.Contains($_) })
    $missing = @($expected | Where-Object { $_ -notin $actual })
    if ($unexpected.Count -gt 0 -or $missing.Count -gt 0) {
        throw 'Unpacked release contents do not exactly match the manifest.'
    }
    return [PSCustomObject]@{
        ReleaseVersion = [string]$manifest.release_version
        SchemaRevision = [string]$manifest.schema_revision
        FileCount = @($manifest.files).Count
        Status = 'RELEASE_VALID'
    }
}

$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$result = Test-TmsReleaseManifest -Workspace $workspace
. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')
$externalValues = @($RuntimeHome, $RuntimeConfigPath, $PythonPath)
$externalValueCount = @($externalValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
if ($externalValueCount -gt 0 -and $externalValueCount -ne 3) {
    throw 'RuntimeHome, RuntimeConfigPath and PythonPath must be supplied together.'
}
if ($externalValueCount -eq 3) {
    $external = Resolve-TmsExternalRuntimeContract -Workspace $workspace `
        -RuntimeHome $RuntimeHome -RuntimeConfigPath $RuntimeConfigPath `
        -PythonPath $PythonPath
    $env:TMS_RUNTIME_HOME = $external.RuntimeHome
    $env:TMS_RUNTIME_CONFIG_PATH = $external.RuntimeConfig
    $env:TMS_PYTHON_PATH = $external.Python
}
if ($ValidateOnly) {
    $result
    return
}
if ($externalValueCount -ne 3) {
    throw 'A runnable Release requires external RuntimeHome, RuntimeConfigPath and PythonPath.'
}
if ($Role -ne 'API' -and (
    -not [string]::IsNullOrWhiteSpace($ListenAddress) -or $Port -ne 0
)) {
    throw 'ListenAddress and Port are valid only for the API role.'
}
if ($Delete -and $Role -notin @('AnalyticsExportCleanup', 'QuickCleanup', 'FormalCleanup')) {
    throw 'Delete is valid only for a cleanup role.'
}

switch ($Role) {
    'API' {
        $apiArguments = @{}
        if (-not [string]::IsNullOrWhiteSpace($ListenAddress)) {
            $apiArguments['ListenAddress'] = $ListenAddress
        }
        if ($Port -ne 0) {
            $apiArguments['Port'] = $Port
        }
        & (Join-Path $PSScriptRoot 'run_tms_api.ps1') @apiArguments
        exit $LASTEXITCODE
    }
    'Worker' { & (Join-Path $PSScriptRoot 'run_tms_worker.ps1'); exit $LASTEXITCODE }
    'AnalyticsExportWorker' { & (Join-Path $PSScriptRoot 'run_tms_analytics_export_worker.ps1'); exit $LASTEXITCODE }
    'FtpCollectionWorker' { & (Join-Path $PSScriptRoot 'run_tms_ftp_collection_worker.ps1'); exit $LASTEXITCODE }
    'AnalyticsExportCleanup' { & (Join-Path $PSScriptRoot 'run_tms_analytics_export_cleanup.ps1') -Delete:$Delete; exit $LASTEXITCODE }
    'QuickCleanup' { & (Join-Path $PSScriptRoot 'run_tms_cleanup.ps1') -DryRun:(-not $Delete); exit $LASTEXITCODE }
    'FormalCleanup' { & (Join-Path $PSScriptRoot 'run_tms_formal_cleanup.ps1') -Delete:$Delete; exit $LASTEXITCODE }
}
