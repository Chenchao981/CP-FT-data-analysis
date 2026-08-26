SET XACT_ABORT ON;
GO

IF SCHEMA_ID('workspace') IS NULL
    EXEC('CREATE SCHEMA workspace AUTHORIZATION dbo');
GO

/*
Quick Analysis is an isolated control plane. It stores provenance and result
metadata only; test.* remains the only Canonical measurement fact chain.
*/
CREATE TABLE workspace.analysis_session (
    analysis_session_id bigint IDENTITY(1,1) NOT NULL,
    owner_user_id bigint NOT NULL,
    analysis_type varchar(32) NOT NULL,
    test_stage varchar(16) NOT NULL,
    factory_code nvarchar(64) NOT NULL,
    source_root_code nvarchar(128) NOT NULL,
    source_relative_path nvarchar(1000) NOT NULL,
    source_manifest_mode varchar(32) NOT NULL,
    source_manifest_json nvarchar(max) NOT NULL,
    source_manifest_sha256 char(64) NOT NULL,
    source_file_count int NOT NULL,
    source_total_bytes bigint NOT NULL,
    retention_mode varchar(32) NOT NULL,
    cleaner_release_id bigint NOT NULL,
    status varchar(32) NOT NULL CONSTRAINT DF_analysis_session_status DEFAULT('QUEUED'),
    parameter_count int NULL,
    record_count bigint NULL,
    summary_json nvarchar(max) NULL,
    error_code nvarchar(64) NULL,
    error_message nvarchar(max) NULL,
    expires_at_utc datetime2(3) NOT NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_analysis_session_created DEFAULT(SYSUTCDATETIME()),
    started_at_utc datetime2(3) NULL,
    finished_at_utc datetime2(3) NULL,
    CONSTRAINT PK_analysis_session PRIMARY KEY CLUSTERED(analysis_session_id),
    CONSTRAINT FK_analysis_session_owner FOREIGN KEY(owner_user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_analysis_session_cleaner_release FOREIGN KEY(cleaner_release_id) REFERENCES ingestion.cleaner_release(cleaner_release_id),
    CONSTRAINT CK_analysis_session_type CHECK(analysis_type IN('QUICK_PAT')),
    CONSTRAINT CK_analysis_session_stage CHECK(test_stage IN('FT')),
    CONSTRAINT CK_analysis_session_manifest_mode CHECK(source_manifest_mode IN('PATH_SIZE_MTIME_V1')),
    CONSTRAINT CK_analysis_session_retention CHECK(retention_mode IN('RESULT_ONLY')),
    CONSTRAINT CK_analysis_session_status CHECK(status IN('QUEUED','RUNNING','SUCCESS','FAILED','CANCELLED','EXPIRED')),
    CONSTRAINT CK_analysis_session_counts CHECK(source_file_count>0 AND source_total_bytes>=0 AND (parameter_count IS NULL OR parameter_count>=0) AND (record_count IS NULL OR record_count>=0)),
    CONSTRAINT CK_analysis_session_error CHECK((status='FAILED' AND error_code IS NOT NULL) OR status<>'FAILED')
);
GO

CREATE NONCLUSTERED INDEX IX_analysis_session_owner_created
ON workspace.analysis_session(owner_user_id,created_at_utc DESC,analysis_session_id DESC)
INCLUDE(status,analysis_type,test_stage,factory_code,source_file_count,source_total_bytes,expires_at_utc);
GO

CREATE NONCLUSTERED INDEX IX_analysis_session_expiry
ON workspace.analysis_session(status,expires_at_utc)
INCLUDE(owner_user_id,analysis_type);
GO

ALTER TABLE ingestion.processing_job ADD analysis_session_id bigint NULL;
GO
ALTER TABLE ingestion.processing_job ADD CONSTRAINT FK_job_analysis_session
    FOREIGN KEY(analysis_session_id) REFERENCES workspace.analysis_session(analysis_session_id);
GO

/* Fail closed if historical data violates the stricter one-input identity. */
IF EXISTS (
    SELECT 1 FROM ingestion.processing_job
    WHERE
        (CASE WHEN source_file_id IS NULL THEN 0 ELSE 1 END) +
        (CASE WHEN import_batch_id IS NULL THEN 0 ELSE 1 END) +
        (CASE WHEN analysis_session_id IS NULL THEN 0 ELSE 1 END) <> 1
)
BEGIN
    RAISERROR('sql2014_0012 blocked: processing_job contains an invalid input identity.', 16, 1);
END;
GO

ALTER TABLE ingestion.processing_job DROP CONSTRAINT CK_job_input;
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_job_input CHECK(
    (CASE WHEN source_file_id IS NULL THEN 0 ELSE 1 END) +
    (CASE WHEN import_batch_id IS NULL THEN 0 ELSE 1 END) +
    (CASE WHEN analysis_session_id IS NULL THEN 0 ELSE 1 END) = 1
);
GO

ALTER TABLE ingestion.processing_job DROP CONSTRAINT CK_job_type;
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_job_type CHECK(job_type IN(
    'INITIAL_IMPORT','EXPORT_LATEST','REPROCESS_UPDATE','DELETE_TASK','QUICK_PAT',
    'PARSE','REPROCESS','REEVALUATE','OTHER'
));
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_processing_job_analysis_session
ON ingestion.processing_job(analysis_session_id)
WHERE analysis_session_id IS NOT NULL;
GO
