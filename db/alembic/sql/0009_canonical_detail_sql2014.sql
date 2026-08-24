IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'analysis')
BEGIN
    EXEC(N'CREATE SCHEMA analysis');
END
GO

SET XACT_ABORT ON;

CREATE TABLE analysis.run (
    run_id bigint IDENTITY(1,1) NOT NULL,
    import_batch_id bigint NOT NULL,
    result_summary_id bigint NULL,
    owner_user_id bigint NOT NULL,
    business_domain varchar(16) NOT NULL,
    test_stage varchar(16) NOT NULL,
    factory_code nvarchar(64) NULL,
    product_name nvarchar(200) NULL,
    lot_id nvarchar(128) NULL,
    unit_count bigint NOT NULL CONSTRAINT DF_run_unit_count DEFAULT(0),
    status varchar(24) NOT NULL CONSTRAINT DF_run_status DEFAULT('ACTIVE'),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_run_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_run PRIMARY KEY CLUSTERED(run_id),
    CONSTRAINT FK_run_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_run_owner FOREIGN KEY(owner_user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_run_domain CHECK(business_domain IN('ENGINEERING','PRODUCTION')),
    CONSTRAINT CK_run_stage CHECK(test_stage IN('CP','FT','WAT','WFT','SLT','QA','ORT','OTHER')),
    CONSTRAINT CK_run_status CHECK(status IN('ACTIVE','SUPERSEDED'))
);
GO

CREATE NONCLUSTERED INDEX IX_run_batch
ON analysis.run(import_batch_id,status,created_at_utc DESC);
GO

CREATE TABLE analysis.test_item (
    test_item_id bigint IDENTITY(1,1) NOT NULL,
    business_domain varchar(16) NOT NULL,
    test_stage varchar(16) NOT NULL,
    factory_code nvarchar(64) NOT NULL,
    name nvarchar(200) NOT NULL,
    unit nvarchar(32) NULL,
    test_condition nvarchar(200) NULL,
    limit_upper decimal(26,10) NULL,
    limit_lower decimal(26,10) NULL,
    fingerprint char(64) NOT NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_test_item_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_test_item PRIMARY KEY CLUSTERED(test_item_id),
    CONSTRAINT UQ_test_item_fingerprint UNIQUE NONCLUSTERED(fingerprint),
    CONSTRAINT CK_test_item_domain CHECK(business_domain IN('ENGINEERING','PRODUCTION')),
    CONSTRAINT CK_test_item_stage CHECK(test_stage IN('CP','FT','WAT','WFT','SLT','QA','ORT','OTHER'))
);
GO

CREATE TABLE analysis.unit (
    unit_id bigint IDENTITY(1,1) NOT NULL,
    run_id bigint NOT NULL,
    lot_id nvarchar(128) NULL,
    wafer_id nvarchar(64) NULL,
    source_id nvarchar(128) NULL,
    seq_no bigint NULL,
    x_coord int NULL,
    y_coord int NULL,
    bin_value int NULL,
    pass_flag bit NULL,
    CONSTRAINT PK_unit PRIMARY KEY CLUSTERED(unit_id),
    CONSTRAINT FK_unit_run FOREIGN KEY(run_id) REFERENCES analysis.run(run_id)
);
GO

CREATE NONCLUSTERED INDEX IX_unit_run_wafer
ON analysis.unit(run_id,wafer_id,x_coord,y_coord)
INCLUDE(bin_value,pass_flag);
GO

CREATE NONCLUSTERED INDEX IX_unit_run_source
ON analysis.unit(run_id,source_id,seq_no);
GO

CREATE TABLE analysis.measurement (
    unit_id bigint NOT NULL,
    test_item_id bigint NOT NULL,
    value decimal(26,10) NOT NULL,
    CONSTRAINT PK_measurement PRIMARY KEY CLUSTERED(unit_id,test_item_id),
    CONSTRAINT FK_measurement_unit FOREIGN KEY(unit_id) REFERENCES analysis.unit(unit_id),
    CONSTRAINT FK_measurement_item FOREIGN KEY(test_item_id) REFERENCES analysis.test_item(test_item_id)
);
GO

CREATE NONCLUSTERED INDEX IX_measurement_item
ON analysis.measurement(test_item_id)
INCLUDE(value);
GO
