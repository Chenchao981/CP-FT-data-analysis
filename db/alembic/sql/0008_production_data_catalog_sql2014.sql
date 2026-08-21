SET XACT_ABORT ON;

ALTER TABLE ingestion.import_batch ADD owner_user_id bigint NULL;
ALTER TABLE ingestion.import_batch ADD business_domain varchar(16) NULL;
ALTER TABLE ingestion.import_batch ADD test_stage varchar(16) NULL;
ALTER TABLE ingestion.import_batch ADD factory_code nvarchar(64) NULL;
ALTER TABLE ingestion.import_batch ADD batch_name nvarchar(300) NULL;
ALTER TABLE ingestion.import_batch ADD remark nvarchar(500) NULL;
GO

ALTER TABLE ingestion.import_batch ADD CONSTRAINT FK_import_batch_owner
FOREIGN KEY(owner_user_id) REFERENCES iam.app_user(user_id);
ALTER TABLE ingestion.import_batch ADD CONSTRAINT CK_import_batch_business_domain
CHECK(business_domain IS NULL OR business_domain IN('ENGINEERING','PRODUCTION'));
ALTER TABLE ingestion.import_batch ADD CONSTRAINT CK_import_batch_test_stage
CHECK(test_stage IS NULL OR test_stage IN('CP','FT','WAT','WFT','SLT','QA','ORT','OTHER'));
GO

CREATE NONCLUSTERED INDEX IX_import_batch_business_list
ON ingestion.import_batch(business_domain,test_stage,owner_user_id,started_at_utc DESC)
INCLUDE(status,factory_code,batch_name,completed_at_utc);
GO

CREATE TABLE ingestion.processing_result_summary (
    result_summary_id bigint IDENTITY(1,1) NOT NULL,
    import_batch_id bigint NOT NULL,
    job_id bigint NULL,
    data_name nvarchar(300) NOT NULL,
    product_name nvarchar(200) NULL,
    lot_id nvarchar(128) NULL,
    wafer_count int NULL,
    factory_code nvarchar(64) NULL,
    output_uri nvarchar(1000) NOT NULL,
    tester_model nvarchar(128) NULL,
    test_item_count int NULL,
    unit_count bigint NULL,
    pass_count bigint NULL,
    yield_rate decimal(12,8) NULL,
    status varchar(24) NOT NULL CONSTRAINT DF_result_summary_status DEFAULT('PROCESSED'),
    data_type varchar(16) NOT NULL,
    artifact_manifest_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_result_summary_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_processing_result_summary PRIMARY KEY CLUSTERED(result_summary_id),
    CONSTRAINT FK_result_summary_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_result_summary_job FOREIGN KEY(job_id) REFERENCES ingestion.processing_job(job_id),
    CONSTRAINT CK_result_summary_stage CHECK(data_type IN('CP','FT','WAT','WFT','OTHER')),
    CONSTRAINT CK_result_summary_status CHECK(status IN('PROCESSED','FAILED','ARCHIVED')),
    CONSTRAINT CK_result_summary_yield CHECK(yield_rate IS NULL OR (yield_rate>=0 AND yield_rate<=1))
);
GO

CREATE NONCLUSTERED INDEX IX_result_summary_batch
ON ingestion.processing_result_summary(import_batch_id,created_at_utc DESC);
GO
