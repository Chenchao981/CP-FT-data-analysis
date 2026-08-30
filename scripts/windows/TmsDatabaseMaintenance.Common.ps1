Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-TmsSafeSqlInstance {
    param([Parameter(Mandatory = $true)][string]$SqlInstance)
    if ($SqlInstance -notmatch '^[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.-]+)?(?:,[0-9]{1,5})?$') {
        throw 'SqlInstance contains unsupported characters.'
    }
}

function Assert-TmsSafeDatabaseName {
    param(
        [Parameter(Mandatory = $true)][string]$Database,
        [Parameter(Mandatory = $true)][string[]]$AllowedDatabases
    )
    if ($Database -notmatch '^[A-Za-z][A-Za-z0-9_]{1,127}$') {
        throw 'Database name is invalid.'
    }
    if ($AllowedDatabases.Count -lt 1) {
        throw 'An explicit database whitelist is required.'
    }
    foreach ($allowed in $AllowedDatabases) {
        if ($allowed -notmatch '^[A-Za-z][A-Za-z0-9_]{1,127}$') {
            throw 'The database whitelist contains an invalid name.'
        }
    }
    if (-not @($AllowedDatabases | Where-Object {
        $_.Equals($Database, [StringComparison]::OrdinalIgnoreCase)
    })) {
        throw "Database is not in the explicit whitelist: $Database"
    }
}

function Assert-TmsNoReparseAncestors {
    param([Parameter(Mandatory = $true)][string]$Path)
    $cursor = if (Test-Path -LiteralPath $Path) {
        Get-Item -LiteralPath $Path -Force
    } else {
        Get-Item -LiteralPath (Split-Path -Parent $Path) -Force
    }
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Path contains a symbolic link or reparse point: $($cursor.FullName)"
        }
        $cursor = if ($cursor -is [IO.DirectoryInfo]) {
            $cursor.Parent
        } else {
            $cursor.Directory
        }
    }
}

function Resolve-TmsBackupPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$MustExist,
        [switch]$MustNotExist
    )
    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw 'Backup path must be absolute.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ([IO.Path]::GetExtension($full) -ne '.bak') {
        throw 'Backup path must use the .bak extension.'
    }
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Backup parent directory does not exist: $parent"
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw "Backup file does not exist: $full"
    }
    if ($MustNotExist -and (Test-Path -LiteralPath $full)) {
        throw "Backup target already exists and will not be overwritten: $full"
    }
    Assert-TmsNoReparseAncestors -Path $full
    return $full
}

function New-TmsIntegratedConnection {
    param(
        [Parameter(Mandatory = $true)][string]$SqlInstance,
        [Parameter(Mandatory = $true)][string]$Database,
        [switch]$TrustServerCertificate
    )
    Assert-TmsSafeSqlInstance -SqlInstance $SqlInstance
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    $builder['Data Source'] = $SqlInstance
    $builder['Initial Catalog'] = $Database
    $builder['Integrated Security'] = $true
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = [bool]$TrustServerCertificate
    $builder['Application Name'] = 'NCE TMS Database Maintenance'
    $builder['Connect Timeout'] = 15
    return New-Object System.Data.SqlClient.SqlConnection -ArgumentList $builder.ConnectionString
}

function Add-TmsSqlParameters {
    param(
        [Parameter(Mandatory = $true)][System.Data.SqlClient.SqlCommand]$Command,
        [hashtable]$Parameters = @{}
    )
    foreach ($name in $Parameters.Keys) {
        $value = $Parameters[$name]
        if ($null -eq $value) {
            $value = [DBNull]::Value
        }
        [void]$Command.Parameters.AddWithValue("@$name", $value)
    }
}

function Invoke-TmsSqlNonQuery {
    param(
        [Parameter(Mandatory = $true)][string]$SqlInstance,
        [Parameter(Mandatory = $true)][string]$Database,
        [Parameter(Mandatory = $true)][string]$Sql,
        [hashtable]$Parameters = @{},
        [int]$CommandTimeout = 0,
        [switch]$TrustServerCertificate
    )
    $connection = New-TmsIntegratedConnection -SqlInstance $SqlInstance -Database $Database -TrustServerCertificate:$TrustServerCertificate
    try {
        $connection.Open()
        $command = $connection.CreateCommand()
        $command.CommandText = $Sql
        $command.CommandTimeout = $CommandTimeout
        Add-TmsSqlParameters -Command $command -Parameters $Parameters
        [void]$command.ExecuteNonQuery()
    } finally {
        if ($null -ne $connection) {
            $connection.Dispose()
        }
    }
}

function Invoke-TmsSqlTable {
    param(
        [Parameter(Mandatory = $true)][string]$SqlInstance,
        [Parameter(Mandatory = $true)][string]$Database,
        [Parameter(Mandatory = $true)][string]$Sql,
        [hashtable]$Parameters = @{},
        [int]$CommandTimeout = 30,
        [switch]$TrustServerCertificate
    )
    $connection = New-TmsIntegratedConnection -SqlInstance $SqlInstance -Database $Database -TrustServerCertificate:$TrustServerCertificate
    try {
        $connection.Open()
        $command = $connection.CreateCommand()
        $command.CommandText = $Sql
        $command.CommandTimeout = $CommandTimeout
        Add-TmsSqlParameters -Command $command -Parameters $Parameters
        $table = New-Object System.Data.DataTable
        $adapter = New-Object System.Data.SqlClient.SqlDataAdapter($command)
        try {
            [void]$adapter.Fill($table)
        } finally {
            $adapter.Dispose()
        }
        return ,$table
    } finally {
        if ($null -ne $connection) {
            $connection.Dispose()
        }
    }
}

function Test-TmsDatabaseExists {
    param(
        [Parameter(Mandatory = $true)][string]$SqlInstance,
        [Parameter(Mandatory = $true)][string]$Database,
        [switch]$TrustServerCertificate
    )
    $table = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database 'master' -Sql (
        'SELECT COUNT_BIG(*) AS database_count FROM sys.databases WHERE name=@database'
    ) -Parameters @{ database = $Database } -TrustServerCertificate:$TrustServerCertificate
    return [int64]$table.Rows[0].database_count -eq 1
}

function Test-TmsDatabaseConsistency {
    param(
        [Parameter(Mandatory = $true)][string]$SqlInstance,
        [Parameter(Mandatory = $true)][string]$Database,
        [Parameter(Mandatory = $true)][string]$ExpectedSchemaRevision,
        [switch]$TrustServerCertificate
    )
    if ($ExpectedSchemaRevision -notmatch '^sql2014_[0-9]{4}$') {
        throw 'ExpectedSchemaRevision is invalid.'
    }
    $revision = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database $Database -Sql @'
SELECT CASE WHEN OBJECT_ID(N'dbo.alembic_version', N'U') IS NULL THEN N''
            ELSE (SELECT TOP(1) version_num FROM dbo.alembic_version) END AS version_num;
'@ -TrustServerCertificate:$TrustServerCertificate
    $actualRevision = [string]$revision.Rows[0].version_num
    if ($actualRevision -ne $ExpectedSchemaRevision) {
        throw "Schema revision mismatch. Expected $ExpectedSchemaRevision, observed $actualRevision."
    }
    $issues = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database $Database -Sql @'
SELECT
    (SELECT COUNT_BIG(*) FROM dataset.dataset_version
      WHERE is_current=1 AND status<>'PUBLISHED') AS invalid_dataset_current,
    (SELECT COUNT_BIG(*) FROM (
        SELECT dataset_id FROM dataset.dataset_version
        WHERE is_current=1 GROUP BY dataset_id HAVING COUNT_BIG(*)>1
     ) duplicate_dataset) AS duplicate_dataset_current,
    (SELECT COUNT_BIG(*) FROM ingestion.processing_run
      WHERE is_current=1 AND status<>'PUBLISHED') AS invalid_run_current,
    (SELECT COUNT_BIG(*) FROM dataset.dataset_version dv
      WHERE dv.status='PUBLISHED' AND dv.is_current=1
        AND NOT EXISTS(
            SELECT 1 FROM dataset.dataset_version_run dvr
            WHERE dvr.dataset_version_id=dv.dataset_version_id
        )) AS current_dataset_without_run,
    (SELECT COUNT_BIG(*) FROM dataset.dataset_version dv
      WHERE dv.status='PUBLISHED' AND dv.is_current=1
        AND EXISTS(
            SELECT 1 FROM dataset.dataset_version_run dvr
            JOIN ingestion.processing_run pr
              ON pr.processing_run_id=dvr.processing_run_id
            WHERE dvr.dataset_version_id=dv.dataset_version_id
              AND (pr.status<>'PUBLISHED' OR pr.is_current<>1)
        )) AS current_dataset_run_mismatch,
    (SELECT COUNT_BIG(*) FROM ingestion.processing_run pr
      WHERE pr.status IN('PUBLISHED','SUPERSEDED')
        AND NOT EXISTS(
            SELECT 1 FROM dataset.dataset_version_run dvr
            JOIN dataset.dataset_version dv
              ON dv.dataset_version_id=dvr.dataset_version_id
            WHERE dvr.processing_run_id=pr.processing_run_id
              AND dv.status='PUBLISHED' AND dv.is_current=1
        )
        AND (pr.status<>'SUPERSEDED' OR pr.is_current<>0)
    ) AS run_without_current_dataset_state_mismatch;
'@ -TrustServerCertificate:$TrustServerCertificate
    $row = $issues.Rows[0]
    $invalidDatasetCurrent = [int64]$row.invalid_dataset_current
    $duplicateDatasetCurrent = [int64]$row.duplicate_dataset_current
    $invalidRunCurrent = [int64]$row.invalid_run_current
    $currentDatasetWithoutRun = [int64]$row.current_dataset_without_run
    $currentDatasetRunMismatch = [int64]$row.current_dataset_run_mismatch
    $runWithoutCurrentDatasetStateMismatch = (
        [int64]$row.run_without_current_dataset_state_mismatch
    )
    $datasetCurrentIssues = (
        $invalidDatasetCurrent +
        $duplicateDatasetCurrent +
        $currentDatasetWithoutRun +
        $currentDatasetRunMismatch
    )
    $processingRunCurrentIssues = (
        $invalidRunCurrent +
        $runWithoutCurrentDatasetStateMismatch
    )
    $total = $datasetCurrentIssues + $processingRunCurrentIssues
    if ($total -ne 0) {
        throw (
            "Dataset Current consistency check failed: total=$total; " +
            "invalid_dataset_current=$invalidDatasetCurrent; " +
            "duplicate_dataset_current=$duplicateDatasetCurrent; " +
            "invalid_run_current=$invalidRunCurrent; " +
            "current_dataset_without_run=$currentDatasetWithoutRun; " +
            "current_dataset_run_mismatch=$currentDatasetRunMismatch; " +
            "run_without_current_dataset_state_mismatch=" +
            "$runWithoutCurrentDatasetStateMismatch."
        )
    }
    return [PSCustomObject]@{
        Database = $Database
        SchemaRevision = $actualRevision
        InvalidDatasetCurrentIssues = $invalidDatasetCurrent
        DuplicateDatasetCurrentIssues = $duplicateDatasetCurrent
        InvalidProcessingRunCurrentIssues = $invalidRunCurrent
        CurrentDatasetWithoutRunIssues = $currentDatasetWithoutRun
        CurrentDatasetRunMismatchIssues = $currentDatasetRunMismatch
        RunWithoutCurrentDatasetStateMismatchIssues = $runWithoutCurrentDatasetStateMismatch
        DatasetCurrentIssues = $datasetCurrentIssues
        ProcessingRunCurrentIssues = $processingRunCurrentIssues
        TotalIssues = $total
        Status = 'CONSISTENT'
    }
}
