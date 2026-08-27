SET XACT_ABORT ON;
GO

/*
Quick Analysis capacity reservations protect the shared work disk while a job
is queued or running. Cleanup metadata preserves evidence after files expire.
*/
ALTER TABLE workspace.analysis_session ADD
    reserved_bytes bigint NOT NULL
        CONSTRAINT DF_analysis_session_reserved DEFAULT(0),
    cleanup_status varchar(16) NOT NULL
        CONSTRAINT DF_analysis_session_cleanup_status DEFAULT('RETAINED'),
    cleanup_attempt_count int NOT NULL
        CONSTRAINT DF_analysis_session_cleanup_attempt DEFAULT(0),
    cleanup_attempted_at_utc datetime2(3) NULL,
    cleaned_at_utc datetime2(3) NULL,
    cleanup_error nvarchar(2000) NULL;
GO

ALTER TABLE workspace.analysis_session ADD
    CONSTRAINT CK_analysis_session_reserved CHECK(reserved_bytes>=0),
    CONSTRAINT CK_analysis_session_cleanup_status CHECK(
        cleanup_status IN('RETAINED','CLEANING','CLEANED','BLOCKED','ERROR')
    ),
    CONSTRAINT CK_analysis_session_cleanup_count CHECK(cleanup_attempt_count>=0);
GO

ALTER TABLE ingestion.processing_artifact ADD
    physical_status varchar(16) NOT NULL
        CONSTRAINT DF_processing_artifact_physical_status DEFAULT('PRESENT'),
    deletion_attempt_count int NOT NULL
        CONSTRAINT DF_processing_artifact_delete_attempt DEFAULT(0),
    deletion_attempted_at_utc datetime2(3) NULL,
    deleted_at_utc datetime2(3) NULL,
    deletion_error nvarchar(2000) NULL;
GO

ALTER TABLE ingestion.processing_artifact ADD
    CONSTRAINT CK_processing_artifact_physical_status CHECK(
        physical_status IN('PRESENT','DELETED','MISSING','BLOCKED','ERROR')
    ),
    CONSTRAINT CK_processing_artifact_delete_count CHECK(deletion_attempt_count>=0);
GO

DROP INDEX IX_processing_artifact_expiry ON ingestion.processing_artifact;
GO

CREATE NONCLUSTERED INDEX IX_processing_artifact_expiry
ON ingestion.processing_artifact(temporary_flag,physical_status,expires_at_utc)
INCLUDE(job_id,storage_uri,file_name,file_size);
GO

CREATE NONCLUSTERED INDEX IX_analysis_session_cleanup
ON workspace.analysis_session(cleanup_status,expires_at_utc,analysis_session_id)
INCLUDE(owner_user_id,status,reserved_bytes);
GO
