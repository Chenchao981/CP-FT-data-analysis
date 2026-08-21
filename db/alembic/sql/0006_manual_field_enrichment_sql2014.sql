SET XACT_ABORT ON;
GO

CREATE TABLE ingestion.field_enrichment (
    enrichment_id bigint IDENTITY(1,1) NOT NULL,
    import_batch_id bigint NOT NULL,
    source_file_id bigint NULL,
    test_stage varchar(16) NOT NULL,
    field_code varchar(64) NOT NULL,
    action varchar(16) NOT NULL,
    value_text nvarchar(500) NULL,
    entered_by bigint NOT NULL,
    reason nvarchar(500) NOT NULL,
    is_current bit NOT NULL CONSTRAINT DF_field_enrichment_current DEFAULT(1),
    supersedes_enrichment_id bigint NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_field_enrichment_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_field_enrichment PRIMARY KEY CLUSTERED(enrichment_id),
    CONSTRAINT FK_field_enrichment_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_field_enrichment_source FOREIGN KEY(source_file_id) REFERENCES ingestion.source_file(source_file_id),
    CONSTRAINT FK_field_enrichment_user FOREIGN KEY(entered_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_field_enrichment_supersedes FOREIGN KEY(supersedes_enrichment_id) REFERENCES ingestion.field_enrichment(enrichment_id),
    CONSTRAINT CK_field_enrichment_stage CHECK(test_stage IN('CP','FT')),
    CONSTRAINT CK_field_enrichment_action CHECK(action IN('FILL','IGNORE')),
    CONSTRAINT CK_field_enrichment_value CHECK(
        (action='FILL' AND NULLIF(LTRIM(RTRIM(value_text)),'') IS NOT NULL)
        OR (action='IGNORE' AND value_text IS NULL)
    )
);
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_field_enrichment_current
ON ingestion.field_enrichment(import_batch_id,source_file_id,test_stage,field_code)
WHERE is_current=1;
GO

CREATE NONCLUSTERED INDEX IX_field_enrichment_batch
ON ingestion.field_enrichment(import_batch_id,is_current,test_stage)
INCLUDE(source_file_id,field_code,action,value_text,entered_by,created_at_utc);
GO
