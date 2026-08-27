SET XACT_ABORT ON;
GO

/* A Cleaner attempt may stop without failing while it waits for a confirmed Lot. */
ALTER TABLE ingestion.processing_job DROP CONSTRAINT CK_job_status;
GO
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_job_status CHECK(status IN(
    'QUEUED','RUNNING','NEEDS_INPUT','SUCCESS','FAILED','CANCELLED'
));
GO

/* parent_job_id exists in the core schema; keep the recovery lineage enforced. */
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE parent_object_id=OBJECT_ID(N'ingestion.processing_job')
      AND name=N'FK_job_parent'
)
    ALTER TABLE ingestion.processing_job ADD CONSTRAINT FK_job_parent
        FOREIGN KEY(parent_job_id) REFERENCES ingestion.processing_job(job_id);
GO

/* The Batch is the user-facing current state; Jobs remain immutable attempts. */
ALTER TABLE ingestion.import_batch ADD CONSTRAINT CK_import_batch_status CHECK(status IN(
    'RECEIVED','QUEUED','PROCESSING','NEEDS_INPUT','PROCESSED','FAILED','CANCELLED'
));
GO

CREATE TABLE ingestion.processing_input_request (
    input_request_id bigint IDENTITY(1,1) NOT NULL,
    job_id bigint NOT NULL,
    import_batch_id bigint NOT NULL,
    receipt_id bigint NOT NULL,
    field_code varchar(64) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_processing_input_request_status DEFAULT('OPEN'),
    prompt nvarchar(500) NOT NULL,
    evidence_json nvarchar(max) NULL,
    resolved_enrichment_id bigint NULL,
    resolved_by bigint NULL,
    requested_at_utc datetime2(3) NOT NULL
        CONSTRAINT DF_processing_input_request_requested DEFAULT(SYSUTCDATETIME()),
    resolved_at_utc datetime2(3) NULL,
    CONSTRAINT PK_processing_input_request PRIMARY KEY CLUSTERED(input_request_id),
    CONSTRAINT FK_processing_input_request_job FOREIGN KEY(job_id)
        REFERENCES ingestion.processing_job(job_id),
    CONSTRAINT FK_processing_input_request_batch FOREIGN KEY(import_batch_id)
        REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_processing_input_request_receipt FOREIGN KEY(receipt_id)
        REFERENCES ingestion.source_file_receipt(receipt_id),
    CONSTRAINT FK_processing_input_request_enrichment FOREIGN KEY(resolved_enrichment_id)
        REFERENCES ingestion.field_enrichment(enrichment_id),
    CONSTRAINT FK_processing_input_request_user FOREIGN KEY(resolved_by)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_processing_input_request_field CHECK(field_code='LOT_ID'),
    CONSTRAINT CK_processing_input_request_status CHECK(status IN('OPEN','RESOLVED')),
    CONSTRAINT CK_processing_input_request_resolution CHECK(
        (status='OPEN' AND resolved_enrichment_id IS NULL
            AND resolved_by IS NULL AND resolved_at_utc IS NULL)
        OR
        (status='RESOLVED' AND resolved_enrichment_id IS NOT NULL
            AND resolved_by IS NOT NULL AND resolved_at_utc IS NOT NULL)
    )
);
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_processing_input_request_open
ON ingestion.processing_input_request(import_batch_id,receipt_id,field_code)
WHERE status='OPEN';
GO

CREATE NONCLUSTERED INDEX IX_processing_input_request_batch
ON ingestion.processing_input_request(import_batch_id,status,requested_at_utc)
INCLUDE(job_id,receipt_id,field_code,resolved_enrichment_id,resolved_by,resolved_at_utc);
GO
