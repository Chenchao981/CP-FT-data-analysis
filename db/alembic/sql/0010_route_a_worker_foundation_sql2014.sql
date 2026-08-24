SET XACT_ABORT ON;
GO

/* Route B detail tables may only be retired after the live-data audit proves empty. */
IF EXISTS (SELECT 1 FROM analysis.measurement)
   OR EXISTS (SELECT 1 FROM analysis.unit)
   OR EXISTS (SELECT 1 FROM analysis.test_item)
   OR EXISTS (SELECT 1 FROM analysis.run)
BEGIN
    RAISERROR('sql2014_0010 blocked: Route B analysis detail tables contain data.', 16, 1);
END;
GO

DROP TABLE analysis.measurement;
DROP TABLE analysis.unit;
DROP TABLE analysis.test_item;
DROP TABLE analysis.run;
GO

/* Missing Product or Lot is a supported Route A business state. */
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'mdm.test_program')
      AND name=N'CK_test_program_stage_identity'
)
    ALTER TABLE mdm.test_program DROP CONSTRAINT CK_test_program_stage_identity;
GO

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'test.test_run')
      AND name=N'CK_test_run_stage_identity'
)
    ALTER TABLE test.test_run DROP CONSTRAINT CK_test_run_stage_identity;
GO

/* Executable Cleaner Release contract. */
ALTER TABLE ingestion.format_profile ADD factory_code nvarchar(64) NULL;
GO

CREATE NONCLUSTERED INDEX IX_format_profile_release_lookup
ON ingestion.format_profile(test_stage,factory_code,status,created_at_utc DESC)
INCLUDE(format_code,profile_version);
GO

ALTER TABLE ingestion.cleaner_release ADD runtime_uri nvarchar(1000) NULL;
ALTER TABLE ingestion.cleaner_release ADD entrypoint nvarchar(300) NULL;
ALTER TABLE ingestion.cleaner_release ADD adapter_code nvarchar(128) NULL;
ALTER TABLE ingestion.cleaner_release ADD input_contract_version nvarchar(64) NULL;
ALTER TABLE ingestion.cleaner_release ADD output_contract_version nvarchar(64) NULL;
ALTER TABLE ingestion.cleaner_release ADD execution_config_json nvarchar(max) NULL;
ALTER TABLE ingestion.cleaner_release ADD timeout_seconds int NOT NULL
    CONSTRAINT DF_cleaner_release_timeout DEFAULT(3600);
ALTER TABLE ingestion.cleaner_release ADD max_output_bytes bigint NOT NULL
    CONSTRAINT DF_cleaner_release_max_output DEFAULT(10737418240);
GO

ALTER TABLE ingestion.cleaner_release ADD CONSTRAINT CK_cleaner_release_timeout
    CHECK(timeout_seconds BETWEEN 1 AND 86400);
ALTER TABLE ingestion.cleaner_release ADD CONSTRAINT CK_cleaner_release_max_output
    CHECK(max_output_bytes>0);
GO

/* Reliable SQL Server queue fields. Existing jobs remain valid historical rows. */
ALTER TABLE ingestion.processing_job ADD requested_by_user_id bigint NULL;
ALTER TABLE ingestion.processing_job ADD idempotency_key nvarchar(128) NULL;
ALTER TABLE ingestion.processing_job ADD not_before_utc datetime2(3) NOT NULL
    CONSTRAINT DF_processing_job_not_before DEFAULT(SYSUTCDATETIME());
ALTER TABLE ingestion.processing_job ADD lease_token uniqueidentifier NULL;
ALTER TABLE ingestion.processing_job ADD lease_owner nvarchar(128) NULL;
ALTER TABLE ingestion.processing_job ADD lease_expires_at_utc datetime2(3) NULL;
ALTER TABLE ingestion.processing_job ADD heartbeat_at_utc datetime2(3) NULL;
ALTER TABLE ingestion.processing_job ADD attempt_count int NOT NULL
    CONSTRAINT DF_processing_job_attempt_count DEFAULT(0);
ALTER TABLE ingestion.processing_job ADD max_attempts int NOT NULL
    CONSTRAINT DF_processing_job_max_attempts DEFAULT(3);
GO

ALTER TABLE ingestion.processing_job ADD CONSTRAINT FK_job_requested_by_user
    FOREIGN KEY(requested_by_user_id) REFERENCES iam.app_user(user_id);
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_processing_job_attempts
    CHECK(attempt_count>=0 AND max_attempts BETWEEN 1 AND 20 AND attempt_count<=max_attempts);
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_processing_job_lease
    CHECK(
        (lease_token IS NULL AND lease_owner IS NULL AND lease_expires_at_utc IS NULL)
        OR
        (lease_token IS NOT NULL AND lease_owner IS NOT NULL AND lease_expires_at_utc IS NOT NULL)
    );
GO

ALTER TABLE ingestion.processing_job DROP CONSTRAINT CK_job_type;
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_job_type CHECK(job_type IN(
    'INITIAL_IMPORT','EXPORT_LATEST','REPROCESS_UPDATE','DELETE_TASK',
    'PARSE','REPROCESS','REEVALUATE','OTHER'
));
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_processing_job_idempotency
ON ingestion.processing_job(idempotency_key)
WHERE idempotency_key IS NOT NULL;
GO

CREATE NONCLUSTERED INDEX IX_processing_job_worker_queue
ON ingestion.processing_job(status,not_before_utc,requested_at_utc,job_id)
INCLUDE(job_type,import_batch_id,cleaner_release_id,attempt_count,max_attempts,lease_expires_at_utc);
GO

/* Temporary and diagnostic artifacts are run records, not permanent facts. */
CREATE TABLE ingestion.processing_artifact (
    processing_artifact_id bigint IDENTITY(1,1) NOT NULL,
    job_id bigint NOT NULL,
    processing_run_id bigint NULL,
    artifact_role nvarchar(64) NOT NULL,
    file_name nvarchar(500) NOT NULL,
    storage_uri nvarchar(1000) NOT NULL,
    file_size bigint NOT NULL,
    sha256 char(64) NOT NULL,
    temporary_flag bit NOT NULL CONSTRAINT DF_processing_artifact_temporary DEFAULT(1),
    expires_at_utc datetime2(3) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_processing_artifact_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_processing_artifact PRIMARY KEY CLUSTERED(processing_artifact_id),
    CONSTRAINT FK_processing_artifact_job FOREIGN KEY(job_id) REFERENCES ingestion.processing_job(job_id),
    CONSTRAINT FK_processing_artifact_run FOREIGN KEY(processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT CK_processing_artifact_size CHECK(file_size>=0),
    CONSTRAINT UQ_processing_artifact UNIQUE(job_id,artifact_role,sha256)
);
GO

CREATE NONCLUSTERED INDEX IX_processing_artifact_expiry
ON ingestion.processing_artifact(temporary_flag,expires_at_utc)
INCLUDE(job_id,storage_uri,file_name);
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_processing_result_summary_job
ON ingestion.processing_result_summary(job_id)
WHERE job_id IS NOT NULL;
GO
