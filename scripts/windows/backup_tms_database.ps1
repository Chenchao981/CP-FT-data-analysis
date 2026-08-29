[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SqlInstance,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string[]]$AllowedDatabases,
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [switch]$TrustServerCertificate,
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TmsDatabaseMaintenance.Common.ps1')

Assert-TmsSafeSqlInstance -SqlInstance $SqlInstance
Assert-TmsSafeDatabaseName -Database $Database -AllowedDatabases $AllowedDatabases
$resolvedBackup = Resolve-TmsBackupPath -Path $BackupPath -MustNotExist
$databaseIdentifier = '[' + $Database.Replace(']', ']]') + ']'
$backupSql = "BACKUP DATABASE $databaseIdentifier TO DISK=@backup_path WITH COPY_ONLY,CHECKSUM,INIT,STATS=10;"
$verifySql = 'RESTORE VERIFYONLY FROM DISK=@backup_path WITH CHECKSUM;'

if (-not $Execute) {
    [PSCustomObject]@{
        Mode = 'DryRun'
        SqlInstance = $SqlInstance
        Database = $Database
        BackupPath = $resolvedBackup
        Operations = @('BACKUP COPY_ONLY CHECKSUM', 'RESTORE VERIFYONLY CHECKSUM')
        Status = 'VALIDATED'
    }
    return
}
if (-not (Test-TmsDatabaseExists -SqlInstance $SqlInstance -Database $Database -TrustServerCertificate:$TrustServerCertificate)) {
    throw "Source database does not exist: $Database"
}
$schema = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database $Database `
    -Sql 'SELECT TOP(1) version_num FROM dbo.alembic_version;' `
    -TrustServerCertificate:$TrustServerCertificate
$schemaRevision = [string]$schema.Rows[0].version_num
if ($schemaRevision -notmatch '^sql2014_[0-9]{4}$') {
    throw 'Source database schema revision is missing or invalid.'
}
Invoke-TmsSqlNonQuery -SqlInstance $SqlInstance -Database 'master' -Sql $backupSql `
    -Parameters @{ backup_path = $resolvedBackup } -CommandTimeout 0 `
    -TrustServerCertificate:$TrustServerCertificate
if (-not (Test-Path -LiteralPath $resolvedBackup -PathType Leaf)) {
    throw 'SQL Server reported success but the backup file is not visible to this host.'
}
Invoke-TmsSqlNonQuery -SqlInstance $SqlInstance -Database 'master' -Sql $verifySql `
    -Parameters @{ backup_path = $resolvedBackup } -CommandTimeout 0 `
    -TrustServerCertificate:$TrustServerCertificate
[PSCustomObject]@{
    Mode = 'Execute'
    SqlInstance = $SqlInstance
    Database = $Database
    BackupPath = $resolvedBackup
    Bytes = (Get-Item -LiteralPath $resolvedBackup).Length
    SchemaRevision = $schemaRevision
    Checksum = 'REQUESTED'
    VerifyOnly = 'PASSED'
    Status = 'BACKUP_VERIFIED'
}
