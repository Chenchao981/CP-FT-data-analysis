CREATE TABLE ingestion.ftp_collection_state (
    source_definition_id bigint NOT NULL PRIMARY KEY,
    config_json nvarchar(max) NOT NULL,
    scan_requested bit NOT NULL DEFAULT(0),
    next_scan_at_utc datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME()),
    lease_token uniqueidentifier NULL,
    lease_expires_at_utc datetime2(3) NULL,
    worker_id nvarchar(128) NULL,
    last_started_at_utc datetime2(3) NULL,
    last_finished_at_utc datetime2(3) NULL,
    last_status varchar(24) NOT NULL DEFAULT('IDLE'),
    error_code varchar(100) NULL,
    error_message nvarchar(500) NULL,
    consecutive_failures int NOT NULL DEFAULT(0),
    CONSTRAINT FK_ftp_state_source FOREIGN KEY(source_definition_id) REFERENCES ingestion.source_definition(source_definition_id),
    CONSTRAINT CK_ftp_state_status CHECK(last_status IN('IDLE','RUNNING','SUCCESS','FAILED','INTERRUPTED'))
);
GO
CREATE TABLE ingestion.ftp_collection_run (
    collection_run_id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
    source_definition_id bigint NOT NULL,
    lease_token uniqueidentifier NOT NULL,
    worker_id nvarchar(128) NOT NULL,
    status varchar(24) NOT NULL DEFAULT('RUNNING'),
    started_at_utc datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME()),
    finished_at_utc datetime2(3) NULL,
    discovered_count int NOT NULL DEFAULT(0),
    submitted_count int NOT NULL DEFAULT(0),
    error_code varchar(100) NULL,
    error_message nvarchar(500) NULL,
    CONSTRAINT FK_ftp_run_source FOREIGN KEY(source_definition_id) REFERENCES ingestion.source_definition(source_definition_id),
    CONSTRAINT UQ_ftp_run_lease UNIQUE(lease_token),
    CONSTRAINT CK_ftp_run_status CHECK(status IN('RUNNING','SUCCESS','FAILED','INTERRUPTED'))
);
GO
CREATE INDEX IX_ftp_run_source ON ingestion.ftp_collection_run(source_definition_id,collection_run_id DESC);
GO
CREATE TABLE ingestion.ftp_package (
    ftp_package_id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
    source_definition_id bigint NOT NULL,
    package_key char(64) NOT NULL,
    relative_path nvarchar(1000) NOT NULL,
    observed_fingerprint char(64) NOT NULL,
    first_observed_at_utc datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME()),
    last_observed_at_utc datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME()),
    file_count int NOT NULL,
    total_bytes bigint NOT NULL,
    status varchar(24) NOT NULL DEFAULT('WAITING'),
    attempts int NOT NULL DEFAULT(0),
    content_sha256 char(64) NULL,
    submitted_fingerprint char(64) NULL,
    import_batch_id bigint NULL,
    job_id bigint NULL,
    error_code varchar(100) NULL,
    error_message nvarchar(500) NULL,
    CONSTRAINT FK_ftp_package_source FOREIGN KEY(source_definition_id) REFERENCES ingestion.source_definition(source_definition_id),
    CONSTRAINT FK_ftp_package_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_ftp_package_job FOREIGN KEY(job_id) REFERENCES ingestion.processing_job(job_id),
    CONSTRAINT UQ_ftp_package_key UNIQUE(source_definition_id,package_key),
    CONSTRAINT CK_ftp_package_status CHECK(status IN('WAITING','RETRY','FAILED','SUBMITTED','CHANGED')),
    CONSTRAINT CK_ftp_package_size CHECK(file_count>=1 AND total_bytes>=0 AND attempts>=0)
);
GO
CREATE INDEX IX_ftp_package_source ON ingestion.ftp_package(source_definition_id,ftp_package_id DESC);
GO
