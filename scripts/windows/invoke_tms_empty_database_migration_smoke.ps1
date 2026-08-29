[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SqlInstance,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string[]]$AllowedTestDatabases,
    [Parameter(Mandatory = $true)][string[]]$ProductionDatabases,
    [Parameter(Mandatory = $true)][string]$ExpectedSchemaRevision,
    [switch]$TrustServerCertificate,
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TmsDatabaseMaintenance.Common.ps1')
. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $workspace '.conda-env\python.exe'
$alembicConfig = Join-Path $workspace 'db\alembic\alembic.ini'
Assert-TmsSafeSqlInstance -SqlInstance $SqlInstance
Assert-TmsSafeDatabaseName -Database $Database -AllowedDatabases $AllowedTestDatabases
if ($ProductionDatabases.Count -lt 1) {
    throw 'An explicit production database denylist is required.'
}
foreach ($productionDatabase in $ProductionDatabases) {
    if ($productionDatabase -notmatch '^[A-Za-z][A-Za-z0-9_]{1,127}$') {
        throw 'ProductionDatabases contains an invalid database name.'
    }
}
if ($Database -notmatch '(?i)_MIGRATION_TEST$') {
    throw 'Empty-database migration target must end with _MIGRATION_TEST.'
}
if (@($ProductionDatabases | Where-Object {
    $_.Equals($Database, [StringComparison]::OrdinalIgnoreCase)
}).Count -gt 0) {
    throw 'Empty-database migration target is listed as a production database.'
}
$releaseHead = Get-TmsRepositorySchemaHead -Workspace $workspace
if ($ExpectedSchemaRevision -ne $releaseHead) {
    throw "ExpectedSchemaRevision must equal release head $releaseHead."
}
foreach ($requiredFile in @($python, $alembicConfig)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Migration dependency is missing: $requiredFile"
    }
}

if (-not $Execute) {
    [PSCustomObject]@{
        Mode = 'DryRun'
        SqlInstance = $SqlInstance
        Database = $Database
        ExistingDatabasePolicy = 'REQUIRE EMPTY'
        ProductionDatabasePolicy = 'PROHIBITED'
        ExpectedSchemaRevision = $ExpectedSchemaRevision
        Operations = @('VERIFY EMPTY DATABASE', 'ALEMBIC UPGRADE HEAD', 'POST-MIGRATION CURRENT CONSISTENCY')
        Status = 'VALIDATED'
    }
    return
}
if (-not (Test-TmsDatabaseExists -SqlInstance $SqlInstance -Database $Database -TrustServerCertificate:$TrustServerCertificate)) {
    throw 'The approved empty migration test database does not exist.'
}
$tableCount = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database $Database -Sql (
    'SELECT COUNT_BIG(*) AS user_table_count FROM sys.tables WHERE is_ms_shipped=0;'
) -TrustServerCertificate:$TrustServerCertificate
if ([int64]$tableCount.Rows[0].user_table_count -ne 0) {
    throw 'Migration smoke target is not empty; existing databases are never reused.'
}

$escapedServer = [uri]::EscapeDataString($SqlInstance)
$escapedDatabase = [uri]::EscapeDataString($Database)
$trust = if ($TrustServerCertificate) { 'yes' } else { 'no' }
$databaseUrl = "mssql+pyodbc://@$escapedServer/$escapedDatabase" +
    '?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes' +
    "&Encrypt=yes&TrustServerCertificate=$trust"
$previousDatabaseUrl = [Environment]::GetEnvironmentVariable('TMS_DATABASE_URL')
Push-Location -LiteralPath $workspace
try {
    $env:TMS_DATABASE_URL = $databaseUrl
    & $python -m alembic -c $alembicConfig upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic empty-database migration failed with exit code $LASTEXITCODE."
    }
} finally {
    if ($null -eq $previousDatabaseUrl) {
        Remove-Item Env:TMS_DATABASE_URL -ErrorAction SilentlyContinue
    } else {
        $env:TMS_DATABASE_URL = $previousDatabaseUrl
    }
    Pop-Location
}
$consistency = Test-TmsDatabaseConsistency -SqlInstance $SqlInstance -Database $Database `
    -ExpectedSchemaRevision $ExpectedSchemaRevision `
    -TrustServerCertificate:$TrustServerCertificate
[PSCustomObject]@{
    Mode = 'Execute'
    SqlInstance = $SqlInstance
    Database = $Database
    SchemaRevision = $consistency.SchemaRevision
    CurrentConsistency = $consistency.Status
    Status = 'EMPTY_DATABASE_MIGRATION_VERIFIED'
}
