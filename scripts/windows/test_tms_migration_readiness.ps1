[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SqlInstance,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string[]]$AllowedDatabases,
    [Parameter(Mandatory = $true)][ValidateSet('PreMigration', 'PostMigration')][string]$Phase,
    [Parameter(Mandatory = $true)][string]$ExpectedSchemaRevision,
    [switch]$TrustServerCertificate,
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TmsDatabaseMaintenance.Common.ps1')

Assert-TmsSafeSqlInstance -SqlInstance $SqlInstance
Assert-TmsSafeDatabaseName -Database $Database -AllowedDatabases $AllowedDatabases
if ($ExpectedSchemaRevision -notmatch '^sql2014_[0-9]{4}$') {
    throw 'ExpectedSchemaRevision is invalid.'
}
if (-not $Execute) {
    [PSCustomObject]@{
        Mode = 'DryRun'
        Phase = $Phase
        SqlInstance = $SqlInstance
        Database = $Database
        ExpectedSchemaRevision = $ExpectedSchemaRevision
        Checks = if ($Phase -eq 'PreMigration') {
            @('DATABASE ONLINE', 'NO ACTIVE OR QUEUED JOBS', 'NO STAGED FINALIZE INTENTS')
        } else {
            @('EXACT SCHEMA REVISION', 'DATASET CURRENT CONSISTENCY', 'PROCESSING RUN CURRENT CONSISTENCY')
        }
        Status = 'VALIDATED'
    }
    return
}
if (-not (Test-TmsDatabaseExists -SqlInstance $SqlInstance -Database $Database -TrustServerCertificate:$TrustServerCertificate)) {
    throw "Database does not exist: $Database"
}
if ($Phase -eq 'PreMigration') {
    $checks = Invoke-TmsSqlTable -SqlInstance $SqlInstance -Database $Database -Sql @'
DECLARE @staged_intents bigint = 0;
IF OBJECT_ID(N'ingestion.initial_import_finalize_intent', N'U') IS NOT NULL
BEGIN
    EXEC sys.sp_executesql
        N'SELECT @count = COUNT_BIG(*) FROM ingestion.initial_import_finalize_intent WHERE status=''STAGED'';',
        N'@count bigint OUTPUT',
        @count=@staged_intents OUTPUT;
END;
SELECT
    (SELECT COUNT_BIG(*) FROM ingestion.processing_job
      WHERE status IN('QUEUED','RUNNING','NEEDS_INPUT')) AS nonterminal_jobs,
    @staged_intents AS staged_intents;
'@ -TrustServerCertificate:$TrustServerCertificate
    $row = $checks.Rows[0]
    if ([int64]$row.nonterminal_jobs -ne 0) {
        throw 'Pre-migration check blocked: nonterminal processing jobs exist.'
    }
    if ([int64]$row.staged_intents -ne 0) {
        throw 'Pre-migration check blocked: STAGED finalize intents exist.'
    }
    [PSCustomObject]@{
        Mode = 'Execute'
        Phase = $Phase
        Database = $Database
        NonterminalJobs = 0
        StagedFinalizeIntents = 0
        Status = 'READY_FOR_MIGRATION'
    }
    return
}
$consistency = Test-TmsDatabaseConsistency -SqlInstance $SqlInstance -Database $Database `
    -ExpectedSchemaRevision $ExpectedSchemaRevision `
    -TrustServerCertificate:$TrustServerCertificate
[PSCustomObject]@{
    Mode = 'Execute'
    Phase = $Phase
    Database = $Database
    SchemaRevision = $consistency.SchemaRevision
    CurrentConsistency = $consistency.Status
    Status = 'POST_MIGRATION_VERIFIED'
}
