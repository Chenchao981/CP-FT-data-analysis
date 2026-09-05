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

function Assert-TmsRuntimeConfigContainsNoSecretLiterals {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $content = Read-TmsUtf8File -Path $Path
    if ($content -match '(?im)^\s*\$env:TMS_(?:JWT_SECRET|HEALTH_BEARER_TOKEN)\s*=\s*[''\"][^''\"]+[''\"]') {
        throw 'Production runtime configuration must not contain a literal JWT or health token.'
    }
    if ($content -match '(?im)^\s*\$env:TMS_DATABASE_URL\s*=.*(?:\bPWD\s*=|\bpassword\s*=|://[^/@:\s]+:[^@/\s]+@)') {
        throw 'Production runtime configuration must not contain a database password.'
    }
}

function Get-TmsRepositorySchemaHead {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Workspace
    )

    $versions = Join-Path $Workspace 'db\alembic\versions'
    if (-not (Test-Path -LiteralPath $versions -PathType Container)) {
        throw "Missing Alembic versions directory: $versions"
    }
    $revisions = New-Object 'System.Collections.Generic.HashSet[string]'
    $parents = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($file in Get-ChildItem -LiteralPath $versions -Filter 'sql2014_*.py' -File) {
        $content = Read-TmsUtf8File -Path $file.FullName
        $revisionMatch = [regex]::Match(
            $content,
            '(?m)^revision\s*=\s*["''](?<revision>sql2014_\d+)["'']\s*$'
        )
        if (-not $revisionMatch.Success) {
            throw "Migration revision is missing or invalid: $($file.FullName)"
        }
        [void]$revisions.Add($revisionMatch.Groups['revision'].Value)
        $parentMatch = [regex]::Match(
            $content,
            '(?m)^down_revision\s*=\s*["''](?<revision>sql2014_\d+)["'']\s*$'
        )
        if ($parentMatch.Success) {
            [void]$parents.Add($parentMatch.Groups['revision'].Value)
        }
    }
    $heads = @($revisions | Where-Object { -not $parents.Contains($_) })
    if ($heads.Count -ne 1) {
        throw "Expected one Alembic head, found $($heads.Count)."
    }
    return [string]$heads[0]
}

function Resolve-TmsManagedDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw "$Name must be a non-empty absolute path."
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd([char[]]'\/')
    $volumeRoot = [IO.Path]::GetPathRoot($full).TrimEnd([char[]]'\/')
    if ([string]::IsNullOrWhiteSpace($full) -or $full -eq $volumeRoot) {
        throw "$Name cannot be a volume or share root."
    }
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        throw "$Name does not exist as a directory: $full"
    }
    return $full
}

function Assert-TmsNoReparsePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $cursor = New-Object IO.DirectoryInfo($Path)
    while ($null -ne $cursor) {
        $item = Get-Item -LiteralPath $cursor.FullName -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Name contains a symbolic link or reparse point: $($item.FullName)"
        }
        $cursor = $cursor.Parent
    }
}

function Test-TmsPathWithinDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd([char[]]'\/')
    $fullDirectory = [IO.Path]::GetFullPath($Directory).TrimEnd([char[]]'\/')
    $directoryPrefix = $fullDirectory + [IO.Path]::DirectorySeparatorChar
    return (
        $fullPath.Equals($fullDirectory, [StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.StartsWith($directoryPrefix, [StringComparison]::OrdinalIgnoreCase)
    )
}

function Resolve-TmsExternalRuntimePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Directory', 'File')]
        [string]$PathType,
        [Parameter(Mandatory = $true)]
        [string]$Workspace
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw "$Name must be a non-empty absolute path."
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd([char[]]'\/')
    $root = [IO.Path]::GetPathRoot($full).TrimEnd([char[]]'\/')
    if ([string]::IsNullOrWhiteSpace($full) -or $full -eq $root) {
        throw "$Name cannot be a volume or share root."
    }
    $expectedPathType = if ($PathType -eq 'Directory') { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $full -PathType $expectedPathType)) {
        throw "$Name does not exist as a $($PathType.ToLowerInvariant()): $full"
    }
    if ($PathType -eq 'File') {
        $parent = [IO.Path]::GetDirectoryName($full).TrimEnd([char[]]'\/')
        if ($parent -eq $root) {
            throw "$Name cannot be stored directly in a volume or share root."
        }
    }
    Assert-TmsNoReparsePath -Name $Name -Path $full
    if (Test-TmsPathWithinDirectory -Path $full -Directory $Workspace) {
        throw "$Name must be outside the Release root."
    }
    if (
        $PathType -eq 'Directory' -and
        (Test-TmsPathWithinDirectory -Path $Workspace -Directory $full)
    ) {
        throw "$Name must not contain the Release root."
    }
    return $full
}

function Resolve-TmsExternalRuntimeContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Workspace,
        [Parameter(Mandatory = $true)]
        [string]$RuntimeHome,
        [Parameter(Mandatory = $true)]
        [string]$RuntimeConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$PythonPath
    )

    $resolvedRuntimeHome = Resolve-TmsExternalRuntimePath -Name 'RuntimeHome' -Path $RuntimeHome `
        -PathType Directory -Workspace $Workspace
    $config = Resolve-TmsExternalRuntimePath -Name 'RuntimeConfigPath' `
        -Path $RuntimeConfigPath -PathType File -Workspace $Workspace
    $python = Resolve-TmsExternalRuntimePath -Name 'PythonPath' -Path $PythonPath `
        -PathType File -Workspace $Workspace
    return [PSCustomObject]@{
        RuntimeHome = $resolvedRuntimeHome
        RuntimeConfig = $config
        Python = $python
    }
}

function Assert-TmsManagedRootsDoNotOverlap {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Roots
    )

    for ($leftIndex = 0; $leftIndex -lt $Roots.Count; $leftIndex++) {
        for ($rightIndex = $leftIndex + 1; $rightIndex -lt $Roots.Count; $rightIndex++) {
            $left = [string]$Roots[$leftIndex].Path
            $right = [string]$Roots[$rightIndex].Path
            $leftPrefix = $left.TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
            $rightPrefix = $right.TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
            if (
                $left.Equals($right, [StringComparison]::OrdinalIgnoreCase) -or
                $leftPrefix.StartsWith($rightPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                $rightPrefix.StartsWith($leftPrefix, [StringComparison]::OrdinalIgnoreCase)
            ) {
                throw "Managed roots must not overlap: $($Roots[$leftIndex].Name) and $($Roots[$rightIndex].Name)."
            }
        }
    }
}

function Get-TmsManagedRootsFromEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Workspace
    )

    $roots = New-Object 'System.Collections.Generic.List[object]'
    foreach ($definition in @(
        @('Upload', 'TMS_UPLOAD_ROOT'),
        @('Work', 'TMS_WORK_ROOT'),
        @('Quick', 'TMS_QUICK_WORK_ROOT'),
        @('AnalyticsExport', 'TMS_ANALYTICS_EXPORT_ROOT'),
        @('Log', 'TMS_LOG_DIR')
    )) {
        $raw = [Environment]::GetEnvironmentVariable($definition[1])
        $resolved = Resolve-TmsManagedDirectory -Name $definition[1] -Path $raw
        Assert-TmsNoReparsePath -Name $definition[1] -Path $resolved
        $roots.Add([PSCustomObject]@{
            Name = $definition[0]
            Kind = $definition[0]
            Code = $definition[1]
            Path = $resolved
        })
    }

    $rawCatalog = [Environment]::GetEnvironmentVariable('TMS_SOURCE_ROOTS_JSON')
    if ([string]::IsNullOrWhiteSpace($rawCatalog)) {
        throw 'TMS_SOURCE_ROOTS_JSON is required in production.'
    }
    if (-not $rawCatalog.TrimStart().StartsWith('[')) {
        throw 'TMS_SOURCE_ROOTS_JSON must be a JSON array.'
    }
    try {
        $parsedCatalog = $rawCatalog | ConvertFrom-Json
    } catch {
        throw 'TMS_SOURCE_ROOTS_JSON must be valid JSON.'
    }
    if ($parsedCatalog -isnot [Array]) {
        throw 'TMS_SOURCE_ROOTS_JSON must be a JSON array.'
    }
    $catalog = @($parsedCatalog | ForEach-Object { $_ })
    if ($catalog.Count -lt 1) {
        throw 'TMS_SOURCE_ROOTS_JSON must contain at least one managed Source root.'
    }
    $allowedProperties = @(
        'code', 'name', 'path', 'purpose', 'business_domains',
        'test_stage', 'factory_code', 'allowed_suffixes', 'data_domain_code'
    )
    $sourceCodes = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($source in $catalog) {
        $unknown = @($source.PSObject.Properties.Name | Where-Object { $_ -notin $allowedProperties })
        if ($unknown.Count -gt 0) {
            throw "Source root contains unsupported fields: $($unknown -join ', ')."
        }
        $code = [string]$source.code
        if ($code -notmatch '^[A-Z0-9][A-Z0-9_-]{1,127}$') {
            throw 'Source root code is missing or invalid.'
        }
        if (-not $sourceCodes.Add($code)) {
            throw "Source root code is duplicated: $code"
        }
        $name = [string]$source.name
        $purpose = ([string]$source.purpose).ToUpperInvariant()
        $stage = ([string]$source.test_stage).ToUpperInvariant()
        $factory = ([string]$source.factory_code).ToUpperInvariant()
        $dataDomainCode = ''
        $dataDomainProperty = $source.PSObject.Properties['data_domain_code']
        if ($null -ne $dataDomainProperty) {
            $dataDomainCode = ([string]$dataDomainProperty.Value).ToUpperInvariant()
        }
        if (
            [string]::IsNullOrWhiteSpace($name) -or
            $name -match '(?i)(__|<|>|replace|placeholder)' -or
            $purpose -notin @('QUICK_ANALYSIS', 'FORMAL_IMPORT') -or
            $stage -notin @('CP', 'FT') -or
            $factory -notmatch '^[A-Z0-9][A-Z0-9_-]{1,63}$'
        ) {
            throw "Source root $code has invalid scope metadata."
        }
        $suffixes = @($source.allowed_suffixes)
        if (
            $suffixes.Count -lt 1 -or
            @($suffixes | Where-Object { [string]$_ -notmatch '^\.[A-Za-z0-9]{1,12}$' }).Count -gt 0
        ) {
            throw "Source root $code has invalid allowed_suffixes."
        }
        $domains = @($source.business_domains)
        if ($purpose -eq 'FORMAL_IMPORT') {
            if (
                $domains.Count -lt 1 -or
                @($domains | Where-Object { [string]$_ -notin @('ENGINEERING', 'PRODUCTION') }).Count -gt 0
            ) {
                throw "Formal Source root $code has invalid business_domains."
            }
            if ($dataDomainCode -notmatch '^[A-Z0-9][A-Z0-9_-]{1,127}$') {
                throw "Formal Source root $code must define a valid data_domain_code."
            }
        } else {
            if ($domains.Count -gt 0) {
                throw "Quick Analysis Source root $code must not define business_domains."
            }
            if ($dataDomainCode -notmatch '^[A-Z0-9][A-Z0-9_-]{1,127}$') {
                throw "Quick Analysis Source root $code must define a valid data_domain_code."
            }
        }
        $resolved = Resolve-TmsManagedDirectory -Name "Source root $code" -Path ([string]$source.path)
        Assert-TmsNoReparsePath -Name "Source root $code" -Path $resolved
        $roots.Add([PSCustomObject]@{
            Name = "Source:$code"
            Kind = 'Source'
            Code = $code
            Path = $resolved
        })
    }
    $rootArray = $roots.ToArray()
    Assert-TmsManagedRootsDoNotOverlap -Roots $rootArray
    return $rootArray
}

function Assert-TmsProductionRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Workspace
    )

    if ([string]$env:TMS_ENV -ne 'production') {
        throw 'TMS_ENV must be production for the production runtime.'
    }
    if ([string]$env:TMS_AUTH_REQUIRED -notmatch '^(?i:true|1|yes|on)$') {
        throw 'TMS_AUTH_REQUIRED must be true in production.'
    }
    if ([string]$env:TMS_JOB_REPOSITORY -ne 'sql') {
        throw 'TMS_JOB_REPOSITORY must be sql in production.'
    }
    $jwt = [string]$env:TMS_JWT_SECRET
    if (
        $jwt.Length -lt 48 -or
        $jwt -match '(?i)(change|replace|example|placeholder|development|local|secret)'
    ) {
        throw 'TMS_JWT_SECRET must be injected securely and contain at least 48 non-placeholder characters.'
    }
    $databaseUrl = [string]$env:TMS_DATABASE_URL
    if (
        [string]::IsNullOrWhiteSpace($databaseUrl) -or
        $databaseUrl -notmatch '^mssql\+pyodbc://' -or
        $databaseUrl -match '(?i)(\bPWD\s*=|\bpassword\s*=|://[^/@:\s]+:[^@/\s]+@)' -or
        $databaseUrl -notmatch '(?i)(trusted_connection=(?:yes|true)|integrated(?:\+|%20|\s)+security=(?:true|sspi))'
    ) {
        throw 'TMS_DATABASE_URL must use SQL Server Integrated Security and contain no password.'
    }
    $databaseIdentity = [regex]::Match(
        $databaseUrl,
        '^mssql\+pyodbc://@(?<server>[^/]+)/(?<database>[^?]+)(?:\?|$)',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if (-not $databaseIdentity.Success) {
        throw 'TMS_DATABASE_URL must use an anonymous Integrated Security URL with an explicit server and database.'
    }
    foreach ($name in @(
        'TMS_EXPECTED_DATABASE',
        'TMS_EXPECTED_DATABASE_SERVER',
        'TMS_EXPECTED_SCHEMA_REVISION',
        'TMS_WORKER_ID',
        'TMS_WORKER_READY_FILE',
        'TMS_FTP_WORKER_ID',
        'TMS_FTP_WORKER_READY_FILE',
        'TMS_FTP_WORKER_STOP_FILE'
    )) {
        $value = [string][Environment]::GetEnvironmentVariable($name)
        if (
            [string]::IsNullOrWhiteSpace($value) -or
            $value -match '(?i)(__|<|>|change|replace|example|placeholder)'
        ) {
            throw "$name is required and must not contain a placeholder."
        }
    }
    if ([string]$env:TMS_EXPECTED_DATABASE -match '(?i)(DEV|TEST|LOCAL)') {
        throw 'TMS_EXPECTED_DATABASE must identify the approved production database.'
    }
    $head = Get-TmsRepositorySchemaHead -Workspace $Workspace
    if ([string]$env:TMS_EXPECTED_SCHEMA_REVISION -ne $head) {
        throw "TMS_EXPECTED_SCHEMA_REVISION must equal release head $head."
    }
    $urlDatabase = [uri]::UnescapeDataString($databaseIdentity.Groups['database'].Value)
    $urlServer = [uri]::UnescapeDataString($databaseIdentity.Groups['server'].Value)
    if (-not $urlDatabase.Equals([string]$env:TMS_EXPECTED_DATABASE, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'TMS_DATABASE_URL does not name TMS_EXPECTED_DATABASE.'
    }
    if (-not $urlServer.Equals([string]$env:TMS_EXPECTED_DATABASE_SERVER, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'TMS_DATABASE_URL does not name TMS_EXPECTED_DATABASE_SERVER.'
    }
    $readyFile = [string]$env:TMS_WORKER_READY_FILE
    if (-not [IO.Path]::IsPathRooted($readyFile)) {
        throw 'TMS_WORKER_READY_FILE must be an absolute path.'
    }
    $ftpControlPaths = @($readyFile)
    foreach ($ftpControlName in @('TMS_FTP_WORKER_READY_FILE', 'TMS_FTP_WORKER_STOP_FILE')) {
        $ftpControlPath = [string][Environment]::GetEnvironmentVariable($ftpControlName)
        if (-not [IO.Path]::IsPathRooted($ftpControlPath)) {
            throw "$ftpControlName must be an absolute path."
        }
        if ($ftpControlPaths -contains [IO.Path]::GetFullPath($ftpControlPath)) {
            throw 'Worker ready and FTP control paths must be distinct.'
        }
        $ftpControlPaths += [IO.Path]::GetFullPath($ftpControlPath)
    }
    $retention = 0
    if (-not [int]::TryParse([string]$env:TMS_LOG_RETENTION_DAYS, [ref]$retention) -or $retention -lt 1 -or $retention -gt 3650) {
        throw 'TMS_LOG_RETENTION_DAYS must be between 1 and 3650.'
    }
    return Get-TmsManagedRootsFromEnvironment -Workspace $Workspace
}

function Get-TmsRuntimeContext {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Role
    )

    $workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    $runtimeHomeValue = [string]$env:TMS_RUNTIME_HOME
    $runtimeConfigValue = [string]$env:TMS_RUNTIME_CONFIG_PATH
    $pythonValue = [string]$env:TMS_PYTHON_PATH
    $externalValues = @($runtimeHomeValue, $runtimeConfigValue, $pythonValue)
    $externalValueCount = @($externalValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
    $externalRuntime = $externalValueCount -gt 0
    if ($externalRuntime -and $externalValueCount -ne 3) {
        throw 'TMS_RUNTIME_HOME, TMS_RUNTIME_CONFIG_PATH and TMS_PYTHON_PATH must be supplied together.'
    }
    if ($externalRuntime) {
        $external = Resolve-TmsExternalRuntimeContract -Workspace $workspace `
            -RuntimeHome $runtimeHomeValue -RuntimeConfigPath $runtimeConfigValue `
            -PythonPath $pythonValue
        $runtimeHome = $external.RuntimeHome
        $runtimeConfig = $external.RuntimeConfig
        $python = $external.Python
    } else {
        $runtimeHome = $workspace
        $runtimeConfig = Join-Path $workspace '.env.runtime.ps1'
        $python = Join-Path $workspace '.conda-env\python.exe'
    }
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
        RuntimeHome = $runtimeHome
        RuntimeConfig = $runtimeConfig
        Python = $python
        Backend = Join-Path $workspace 'backend'
        LogDir = if ($externalRuntime) { Join-Path $runtimeHome 'logs' } else { Join-Path $workspace 'data\logs' }
        ExternalRuntime = $externalRuntime
    }
}

function Initialize-TmsRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Context
    )

    Import-TmsRuntimeConfig -Path $Context.RuntimeConfig
    if ($Context.ExternalRuntime) {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $env:PYTHONPATH = $Context.Backend
        $env:TMS_LOG_DIR = $Context.LogDir
        New-Item -ItemType Directory -Path $env:TMS_LOG_DIR -Force | Out-Null
    }
    if ([string]$env:TMS_ENV -eq 'production') {
        Assert-TmsRuntimeConfigContainsNoSecretLiterals -Path $Context.RuntimeConfig
        [void](Assert-TmsProductionRuntime -Workspace $Context.Workspace)
    }
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
