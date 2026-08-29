[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SqlInstance,
    [Parameter(Mandatory = $true)][string]$TargetDatabase,
    [Parameter(Mandatory = $true)][string[]]$AllowedTestDatabases,
    [Parameter(Mandatory = $true)][string[]]$ProductionDatabases,
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$RestoreDataDirectory,
    [Parameter(Mandatory = $true)][string]$ExpectedSchemaRevision,
    [switch]$TrustServerCertificate,
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TmsDatabaseMaintenance.Common.ps1')

Assert-TmsSafeSqlInstance -SqlInstance $SqlInstance
Assert-TmsSafeDatabaseName -Database $TargetDatabase -AllowedDatabases $AllowedTestDatabases
if ($ProductionDatabases.Count -lt 1) {
    throw 'An explicit production database denylist is required.'
}
foreach ($productionDatabase in $ProductionDatabases) {
    if ($productionDatabase -notmatch '^[A-Za-z][A-Za-z0-9_]{1,127}$') {
        throw 'ProductionDatabases contains an invalid database name.'
    }
}
if ($TargetDatabase -notmatch '(?i)(?:_RESTORE_TEST|_DR_TEST)$') {
    throw 'Restore target must end with _RESTORE_TEST or _DR_TEST.'
}
if (@($ProductionDatabases | Where-Object {
    $_.Equals($TargetDatabase, [StringComparison]::OrdinalIgnoreCase)
}).Count -gt 0) {
    throw 'Restore target is listed as a production database.'
}
if ($ExpectedSchemaRevision -notmatch '^sql2014_[0-9]{4}$') {
    throw 'ExpectedSchemaRevision is invalid.'
}
$resolvedBackup = Resolve-TmsBackupPath -Path $BackupPath -MustExist
if (-not [IO.Path]::IsPathRooted($RestoreDataDirectory)) {
    throw 'RestoreDataDirectory must be absolute.'
}
$restoreDirectory = [IO.Path]::GetFullPath($RestoreDataDirectory).TrimEnd([char[]]'\/')
if (-not (Test-Path -LiteralPath $restoreDirectory -PathType Container)) {
    throw "Restore data directory does not exist: $restoreDirectory"
}
Assert-TmsNoReparseAncestors -Path $restoreDirectory

if (-not $Execute) {
    [PSCustomObject]@{
        Mode = 'DryRun'
        SqlInstance = $SqlInstance
        TargetDatabase = $TargetDatabase
        BackupPath = $resolvedBackup
        RestoreDataDirectory = $restoreDirectory
        ExistingTargetPolicy = 'REJECT'
        ProductionOverwritePolicy = 'PROHIBITED'
        Operations = @('RESTORE VERIFYONLY CHECKSUM', 'RESTORE FILELISTONLY', 'RESTORE WITH CHECKSUM and explicit MOVE', 'POST-RESTORE CONSISTENCY')
        Status = 'VALIDATED'
    }
    return
}
if (Test-TmsDatabaseExists -SqlInstance $SqlInstance -Database $TargetDatabase -TrustServerCertificate:$TrustServerCertificate) {
    throw 'Restore target already exists; overwrite and WITH REPLACE are prohibited.'
}
Invoke-TmsSqlNonQuery -SqlInstance $SqlInstance -Database 'master' `
    -Sql 'RESTORE VERIFYONLY FROM DISK=@backup_path WITH CHECKSUM;' `
    -Parameters @{ backup_path = $resolvedBackup } -CommandTimeout 0 `
    -TrustServerCertificate:$TrustServerCertificate
$files = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database 'master' `
    -Sql 'RESTORE FILELISTONLY FROM DISK=@backup_path;' `
    -Parameters @{ backup_path = $resolvedBackup } -CommandTimeout 0 `
    -TrustServerCertificate:$TrustServerCertificate
if ($files.Rows.Count -lt 2) {
    throw 'Backup must contain at least one data file and one log file.'
}
$moves = New-Object 'System.Collections.Generic.List[string]'
$ordinal = 0
foreach ($file in $files.Rows) {
    $ordinal += 1
    $logical = [string]$file.LogicalName
    if ([string]::IsNullOrWhiteSpace($logical)) {
        throw 'Backup contains an empty logical file name.'
    }
    $extension = if ([string]$file.Type -eq 'L') { '.ldf' } elseif ($ordinal -eq 1) { '.mdf' } else { ".ndf" }
    $destination = Join-Path $restoreDirectory ("{0}_{1}{2}" -f $TargetDatabase, $ordinal, $extension)
    if (Test-Path -LiteralPath $destination) {
        throw "Restore destination already exists: $destination"
    }
    $escapedLogical = $logical.Replace("'", "''")
    $escapedDestination = $destination.Replace("'", "''")
    $moves.Add("MOVE N'$escapedLogical' TO N'$escapedDestination'")
}
$targetIdentifier = '[' + $TargetDatabase.Replace(']', ']]') + ']'
$restoreSql = "RESTORE DATABASE $targetIdentifier FROM DISK=@backup_path WITH CHECKSUM,RECOVERY,$($moves -join ','),STATS=10;"
Invoke-TmsSqlNonQuery -SqlInstance $SqlInstance -Database 'master' -Sql $restoreSql `
    -Parameters @{ backup_path = $resolvedBackup } -CommandTimeout 0 `
    -TrustServerCertificate:$TrustServerCertificate
$consistency = Test-TmsDatabaseConsistency -SqlInstance $SqlInstance -Database $TargetDatabase `
    -ExpectedSchemaRevision $ExpectedSchemaRevision `
    -TrustServerCertificate:$TrustServerCertificate
[PSCustomObject]@{
    Mode = 'Execute'
    SqlInstance = $SqlInstance
    TargetDatabase = $TargetDatabase
    BackupPath = $resolvedBackup
    VerifyOnly = 'PASSED'
    SchemaRevision = $consistency.SchemaRevision
    CurrentConsistency = $consistency.Status
    Status = 'RESTORE_VERIFIED'
}
