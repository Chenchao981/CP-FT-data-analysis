SET XACT_ABORT ON;
GO

/*
A5 owns logical Dataset lifecycle and temporary formal artifacts only.
Canonical test facts, Source Catalog objects, upload files and Import Batch state
are deliberately outside this migration's deletion boundary.
*/
IF OBJECT_ID(N'dataset.dataset', N'U') IS NULL
    RAISERROR('sql2014_0018 blocked: dataset.dataset is missing.', 16, 1);
IF OBJECT_ID(N'dataset.dataset_version', N'U') IS NULL
    RAISERROR('sql2014_0018 blocked: dataset.dataset_version is missing.', 16, 1);
IF OBJECT_ID(N'ingestion.processing_job', N'U') IS NULL
    RAISERROR('sql2014_0018 blocked: ingestion.processing_job is missing.', 16, 1);
IF OBJECT_ID(N'ingestion.processing_artifact', N'U') IS NULL
    RAISERROR('sql2014_0018 blocked: ingestion.processing_artifact is missing.', 16, 1);
IF OBJECT_ID(N'iam.app_user', N'U') IS NULL
    RAISERROR('sql2014_0018 blocked: iam.app_user is missing.', 16, 1);
GO

IF COL_LENGTH(N'dataset.dataset', N'lifecycle_status') IS NULL
BEGIN
    ALTER TABLE dataset.dataset ADD lifecycle_status varchar(16) NOT NULL
        CONSTRAINT DF_dataset_lifecycle_status DEFAULT('ACTIVE') WITH VALUES;
END;
GO

IF COL_LENGTH(N'dataset.dataset', N'archived_at_utc') IS NULL
    ALTER TABLE dataset.dataset ADD archived_at_utc datetime2(3) NULL;
IF COL_LENGTH(N'dataset.dataset', N'archived_by_user_id') IS NULL
    ALTER TABLE dataset.dataset ADD archived_by_user_id bigint NULL;
IF COL_LENGTH(N'dataset.dataset', N'archive_reason') IS NULL
    ALTER TABLE dataset.dataset ADD archive_reason nvarchar(1000) NULL;
IF COL_LENGTH(N'dataset.dataset', N'lifecycle_row_version') IS NULL
    ALTER TABLE dataset.dataset ADD lifecycle_row_version rowversion NOT NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE parent_object_id=OBJECT_ID(N'dataset.dataset')
      AND name=N'FK_dataset_archived_by'
)
BEGIN
    ALTER TABLE dataset.dataset ADD CONSTRAINT FK_dataset_archived_by
        FOREIGN KEY(archived_by_user_id) REFERENCES iam.app_user(user_id);
END;
GO

IF EXISTS (
    SELECT 1 FROM dataset.dataset
    WHERE lifecycle_status NOT IN('ACTIVE','ARCHIVED')
       OR (lifecycle_status='ACTIVE' AND
           (archived_at_utc IS NOT NULL OR archived_by_user_id IS NOT NULL
            OR archive_reason IS NOT NULL))
       OR (lifecycle_status='ARCHIVED' AND
           (archived_at_utc IS NULL OR archived_by_user_id IS NULL
            OR NULLIF(LTRIM(RTRIM(archive_reason)),N'') IS NULL))
)
    RAISERROR('sql2014_0018 blocked: dataset lifecycle state is inconsistent.',16,1);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'dataset.dataset')
      AND name=N'CK_dataset_lifecycle_status'
)
BEGIN
    ALTER TABLE dataset.dataset ADD CONSTRAINT CK_dataset_lifecycle_status CHECK(
        (lifecycle_status='ACTIVE' AND archived_at_utc IS NULL
         AND archived_by_user_id IS NULL AND archive_reason IS NULL)
        OR
        (lifecycle_status='ARCHIVED' AND archived_at_utc IS NOT NULL
         AND archived_by_user_id IS NOT NULL
         AND NULLIF(LTRIM(RTRIM(archive_reason)),N'') IS NOT NULL)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'dataset.dataset')
      AND name=N'IX_dataset_lifecycle_owner'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_dataset_lifecycle_owner
    ON dataset.dataset(lifecycle_status,owner_user_id,dataset_id)
    INCLUDE(test_stage,archived_at_utc,archived_by_user_id);
END;
GO

/* One immutable target contract per lifecycle Job keeps generic Job DTOs stable. */
IF OBJECT_ID(N'ingestion.lifecycle_job_target', N'U') IS NULL
BEGIN
    CREATE TABLE ingestion.lifecycle_job_target (
        job_id bigint NOT NULL,
        dataset_id bigint NOT NULL,
        target_dataset_version_id bigint NOT NULL,
        action_type varchar(32) NOT NULL,
        requested_by_user_id bigint NOT NULL,
        request_reason nvarchar(1000) NULL,
        created_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_lifecycle_job_target_created DEFAULT(SYSUTCDATETIME()),
        row_version rowversion NOT NULL,
        CONSTRAINT PK_lifecycle_job_target PRIMARY KEY CLUSTERED(job_id),
        CONSTRAINT FK_lifecycle_target_job FOREIGN KEY(job_id)
            REFERENCES ingestion.processing_job(job_id),
        CONSTRAINT FK_lifecycle_target_dataset FOREIGN KEY(dataset_id)
            REFERENCES dataset.dataset(dataset_id),
        CONSTRAINT FK_lifecycle_target_version FOREIGN KEY(target_dataset_version_id)
            REFERENCES dataset.dataset_version(dataset_version_id),
        CONSTRAINT FK_lifecycle_target_requester FOREIGN KEY(requested_by_user_id)
            REFERENCES iam.app_user(user_id),
        CONSTRAINT CK_lifecycle_target_action CHECK(
            action_type IN('EXPORT_LATEST','REPROCESS_UPDATE','DELETE_TASK')
        ),
        CONSTRAINT CK_lifecycle_target_reason CHECK(
            action_type<>'DELETE_TASK'
            OR (request_reason IS NOT NULL
                AND LEN(LTRIM(RTRIM(request_reason))) BETWEEN 8 AND 1000)
        )
    );
END;
GO

IF OBJECT_ID(N'ingestion.lifecycle_job_target', N'U') IS NOT NULL
   AND (
       COL_LENGTH(N'ingestion.lifecycle_job_target', N'job_id') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'dataset_id') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'target_dataset_version_id') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'action_type') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'requested_by_user_id') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'request_reason') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'created_at_utc') IS NULL
       OR COL_LENGTH(N'ingestion.lifecycle_job_target', N'row_version') IS NULL
   )
    RAISERROR('sql2014_0018 blocked: lifecycle_job_target has an incompatible shape.',16,1);
GO

IF OBJECT_ID(N'ingestion.lifecycle_job_target', N'U') IS NOT NULL
   AND (
       NOT EXISTS (
           SELECT 1 FROM sys.key_constraints
           WHERE parent_object_id=OBJECT_ID(N'ingestion.lifecycle_job_target')
             AND name=N'PK_lifecycle_job_target' AND type='PK'
       )
       OR (SELECT COUNT(*) FROM sys.foreign_keys
           WHERE parent_object_id=OBJECT_ID(N'ingestion.lifecycle_job_target')
             AND name IN(
                 N'FK_lifecycle_target_job',N'FK_lifecycle_target_dataset',
                 N'FK_lifecycle_target_version',N'FK_lifecycle_target_requester'
             ))<>4
       OR (SELECT COUNT(*) FROM sys.check_constraints
           WHERE parent_object_id=OBJECT_ID(N'ingestion.lifecycle_job_target')
             AND name IN(
                 N'CK_lifecycle_target_action',N'CK_lifecycle_target_reason'
             ))<>2
   )
    RAISERROR('sql2014_0018 blocked: lifecycle_job_target constraints are incomplete.',16,1);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.lifecycle_job_target')
      AND name=N'IX_lifecycle_target_dataset'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_lifecycle_target_dataset
    ON ingestion.lifecycle_job_target(dataset_id,action_type,created_at_utc DESC,job_id)
    INCLUDE(target_dataset_version_id,requested_by_user_id);
END;
GO

/* 0013 columns are mandatory; do not silently create a partial cleanup contract. */
IF COL_LENGTH(N'ingestion.processing_artifact', N'physical_status') IS NULL
   OR COL_LENGTH(N'ingestion.processing_artifact', N'deletion_attempt_count') IS NULL
   OR COL_LENGTH(N'ingestion.processing_artifact', N'deletion_attempted_at_utc') IS NULL
   OR COL_LENGTH(N'ingestion.processing_artifact', N'deleted_at_utc') IS NULL
   OR COL_LENGTH(N'ingestion.processing_artifact', N'deletion_error') IS NULL
    RAISERROR('sql2014_0018 blocked: sql2014_0013 artifact cleanup columns are missing.',16,1);
GO

/* DELETING is a durable claim; stale claims are recoverable by the cleanup service. */
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'ingestion.processing_artifact')
      AND name=N'CK_processing_artifact_physical_status'
      AND definition NOT LIKE '%DELETING%'
)
BEGIN
    ALTER TABLE ingestion.processing_artifact
        DROP CONSTRAINT CK_processing_artifact_physical_status;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'ingestion.processing_artifact')
      AND name=N'CK_processing_artifact_physical_status'
)
BEGIN
    ALTER TABLE ingestion.processing_artifact ADD
        CONSTRAINT CK_processing_artifact_physical_status CHECK(
            physical_status IN(
                'PRESENT','DELETING','DELETED','MISSING','BLOCKED','ERROR'
            )
        );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.processing_artifact')
      AND name=N'IX_processing_artifact_formal_cleanup'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_processing_artifact_formal_cleanup
    ON ingestion.processing_artifact(
        temporary_flag,physical_status,expires_at_utc,job_id
    )
    INCLUDE(processing_artifact_id,artifact_role,storage_uri,file_size,sha256,
            deletion_attempted_at_utc);
END;
GO

/*
Downgrade is intentionally blocked in the Alembic wrapper: lifecycle audit rows
and archived business state cannot be losslessly projected onto revision 0017.
*/
