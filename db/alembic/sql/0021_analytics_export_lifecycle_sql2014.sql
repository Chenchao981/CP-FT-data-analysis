SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'delivery.export_job', N'U') IS NULL
    RAISERROR('sql2014_0021 blocked: delivery.export_job is missing.',16,1);
IF OBJECT_ID(N'delivery.export_artifact', N'U') IS NULL
    RAISERROR('sql2014_0021 blocked: delivery.export_artifact is missing.',16,1);
IF COL_LENGTH(N'delivery.export_job', N'contract_version') IS NULL
    RAISERROR('sql2014_0021 blocked: sql2014_0020 analytics governance is missing.',16,1);
GO

/* Forward-compatible Worker fencing. Existing queued/history rows keep no lease. */
IF COL_LENGTH(N'delivery.export_job', N'attempt_count') IS NULL
    ALTER TABLE delivery.export_job ADD attempt_count int NOT NULL
        CONSTRAINT DF_export_job_attempt_count DEFAULT(0) WITH VALUES;
IF COL_LENGTH(N'delivery.export_job', N'max_attempts') IS NULL
    ALTER TABLE delivery.export_job ADD max_attempts tinyint NOT NULL
        CONSTRAINT DF_export_job_max_attempts DEFAULT(3) WITH VALUES;
IF COL_LENGTH(N'delivery.export_job', N'lease_token') IS NULL
    ALTER TABLE delivery.export_job ADD lease_token uniqueidentifier NULL;
IF COL_LENGTH(N'delivery.export_job', N'lease_owner') IS NULL
    ALTER TABLE delivery.export_job ADD lease_owner nvarchar(100) NULL;
IF COL_LENGTH(N'delivery.export_job', N'lease_expires_at_utc') IS NULL
    ALTER TABLE delivery.export_job ADD lease_expires_at_utc datetime2(3) NULL;
IF COL_LENGTH(N'delivery.export_job', N'heartbeat_at_utc') IS NULL
    ALTER TABLE delivery.export_job ADD heartbeat_at_utc datetime2(3) NULL;
GO

/*
  A deployment is expected to stop the old Worker before migration. Any legacy
  RUNNING row has no fencing token, so it is safely returned to QUEUED instead
  of being left permanently RUNNING or being finalized without a lease.
*/
UPDATE delivery.export_job
SET status='QUEUED',started_at_utc=NULL,finished_at_utc=NULL,
    error_message='ANALYTICS_EXPORT_MIGRATION_RECOVERY: legacy RUNNING job requeued',
    lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,
    heartbeat_at_utc=NULL
WHERE status='RUNNING' AND lease_token IS NULL;
GO

IF NOT EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'delivery.export_job')
      AND name=N'CK_export_job_attempts'
)
    ALTER TABLE delivery.export_job ADD CONSTRAINT CK_export_job_attempts
    CHECK(attempt_count>=0 AND max_attempts BETWEEN 1 AND 20
          AND attempt_count<=max_attempts);
IF NOT EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'delivery.export_job')
      AND name=N'CK_export_job_lease'
)
    ALTER TABLE delivery.export_job ADD CONSTRAINT CK_export_job_lease CHECK(
        (
            status='RUNNING' AND lease_token IS NOT NULL
            AND lease_owner IS NOT NULL AND lease_expires_at_utc IS NOT NULL
            AND heartbeat_at_utc IS NOT NULL
        ) OR (
            status<>'RUNNING' AND lease_token IS NULL
            AND lease_owner IS NULL AND lease_expires_at_utc IS NULL
            AND heartbeat_at_utc IS NULL
        )
    );
GO

IF NOT EXISTS(
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'delivery.export_job')
      AND name=N'IX_export_job_worker_claim'
)
    CREATE NONCLUSTERED INDEX IX_export_job_worker_claim
    ON delivery.export_job(contract_version,status,requested_at_utc,export_job_id)
    INCLUDE(attempt_count,max_attempts,lease_expires_at_utc,lease_owner);
GO

/* Retain Artifact metadata after physical TTL cleanup. */
IF COL_LENGTH(N'delivery.export_artifact', N'physical_status') IS NULL
    ALTER TABLE delivery.export_artifact ADD physical_status varchar(16) NOT NULL
        CONSTRAINT DF_export_artifact_physical_status DEFAULT('PRESENT') WITH VALUES;
IF COL_LENGTH(N'delivery.export_artifact', N'deletion_attempt_count') IS NULL
    ALTER TABLE delivery.export_artifact ADD deletion_attempt_count int NOT NULL
        CONSTRAINT DF_export_artifact_delete_attempt DEFAULT(0) WITH VALUES;
IF COL_LENGTH(N'delivery.export_artifact', N'deletion_attempted_at_utc') IS NULL
    ALTER TABLE delivery.export_artifact ADD deletion_attempted_at_utc datetime2(3) NULL;
IF COL_LENGTH(N'delivery.export_artifact', N'deleted_at_utc') IS NULL
    ALTER TABLE delivery.export_artifact ADD deleted_at_utc datetime2(3) NULL;
IF COL_LENGTH(N'delivery.export_artifact', N'deletion_reason') IS NULL
    ALTER TABLE delivery.export_artifact ADD deletion_reason nvarchar(1000) NULL;
GO

IF NOT EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'delivery.export_artifact')
      AND name=N'CK_export_artifact_physical_status'
)
    ALTER TABLE delivery.export_artifact ADD
    CONSTRAINT CK_export_artifact_physical_status CHECK(
        physical_status IN('PRESENT','DELETING','DELETED','MISSING','BLOCKED','ERROR')
    );
IF NOT EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'delivery.export_artifact')
      AND name=N'CK_export_artifact_delete_count'
)
    ALTER TABLE delivery.export_artifact ADD
    CONSTRAINT CK_export_artifact_delete_count CHECK(deletion_attempt_count>=0);
IF NOT EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'delivery.export_artifact')
      AND name=N'CK_export_artifact_deleted_at'
)
    ALTER TABLE delivery.export_artifact ADD
    CONSTRAINT CK_export_artifact_deleted_at CHECK(
        (physical_status IN('DELETED','MISSING') AND deleted_at_utc IS NOT NULL)
        OR (physical_status NOT IN('DELETED','MISSING') AND deleted_at_utc IS NULL)
    );
GO

IF NOT EXISTS(
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'delivery.export_artifact')
      AND name=N'IX_export_artifact_ttl_cleanup'
)
    CREATE NONCLUSTERED INDEX IX_export_artifact_ttl_cleanup
    ON delivery.export_artifact(physical_status,expires_at_utc,export_job_id)
    INCLUDE(storage_uri,file_size,sha256,deletion_attempted_at_utc);
GO

/* Alembic verifies its version-row UPDATE through pyodbc rowcount. */
SET NOCOUNT OFF;
GO
