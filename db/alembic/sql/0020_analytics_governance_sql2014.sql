SET XACT_ABORT ON;
GO

/*
v1.3 analytics governance extends the historical 0002 skeleton.  It stores
versioned rules and reproducible Context metadata only; test.* remains the sole
formal Measurement fact chain.
*/
IF OBJECT_ID(N'evaluation.rule_set', N'U') IS NULL
    RAISERROR('sql2014_0020 blocked: evaluation.rule_set is missing.',16,1);
IF OBJECT_ID(N'evaluation.rule_version', N'U') IS NULL
    RAISERROR('sql2014_0020 blocked: evaluation.rule_version is missing.',16,1);
IF OBJECT_ID(N'analysis.saved_analysis', N'U') IS NULL
    RAISERROR('sql2014_0020 blocked: analysis.saved_analysis is missing.',16,1);
IF OBJECT_ID(N'delivery.export_job', N'U') IS NULL
    RAISERROR('sql2014_0020 blocked: delivery.export_job is missing.',16,1);
IF OBJECT_ID(N'dataset.dataset_version', N'U') IS NULL
    RAISERROR('sql2014_0020 blocked: dataset.dataset_version is missing.',16,1);
IF OBJECT_ID(N'iam.app_user', N'U') IS NULL
    RAISERROR('sql2014_0020 blocked: iam.app_user is missing.',16,1);
GO

IF COL_LENGTH(N'evaluation.rule_set', N'business_owner_user_id') IS NULL
    ALTER TABLE evaluation.rule_set ADD business_owner_user_id bigint NULL;
IF COL_LENGTH(N'evaluation.rule_set', N'technical_owner_user_id') IS NULL
    ALTER TABLE evaluation.rule_set ADD technical_owner_user_id bigint NULL;
IF COL_LENGTH(N'evaluation.rule_set', N'quality_validator_user_id') IS NULL
    ALTER TABLE evaluation.rule_set ADD quality_validator_user_id bigint NULL;
IF COL_LENGTH(N'evaluation.rule_set', N'description') IS NULL
    ALTER TABLE evaluation.rule_set ADD description nvarchar(1000) NULL;
IF COL_LENGTH(N'evaluation.rule_set', N'active') IS NULL
    ALTER TABLE evaluation.rule_set ADD active bit NOT NULL
        CONSTRAINT DF_evaluation_rule_set_active DEFAULT(1) WITH VALUES;
GO

IF NOT EXISTS(SELECT 1 FROM sys.foreign_keys WHERE name=N'FK_evaluation_rule_business_owner')
    ALTER TABLE evaluation.rule_set ADD CONSTRAINT FK_evaluation_rule_business_owner
        FOREIGN KEY(business_owner_user_id) REFERENCES iam.app_user(user_id);
IF NOT EXISTS(SELECT 1 FROM sys.foreign_keys WHERE name=N'FK_evaluation_rule_technical_owner')
    ALTER TABLE evaluation.rule_set ADD CONSTRAINT FK_evaluation_rule_technical_owner
        FOREIGN KEY(technical_owner_user_id) REFERENCES iam.app_user(user_id);
IF NOT EXISTS(SELECT 1 FROM sys.foreign_keys WHERE name=N'FK_evaluation_rule_quality_validator')
    ALTER TABLE evaluation.rule_set ADD CONSTRAINT FK_evaluation_rule_quality_validator
        FOREIGN KEY(quality_validator_user_id) REFERENCES iam.app_user(user_id);
GO

IF EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'evaluation.rule_set')
      AND name=N'CK_evaluation_rule_type'
)
    ALTER TABLE evaluation.rule_set DROP CONSTRAINT CK_evaluation_rule_type;
GO
ALTER TABLE evaluation.rule_set ADD CONSTRAINT CK_evaluation_rule_type CHECK(
    evaluation_type IN(
        'SPEC','BIN','PAT','SBL','CPK','SPC','HISTOGRAM','BOX_PLOT',
        'NORMAL_FIT','CORRELATION','MARGIN','ZONE','BIN_COOCCURRENCE','OTHER'
    )
);
GO

IF COL_LENGTH(N'evaluation.rule_version', N'applicability_json') IS NULL
    ALTER TABLE evaluation.rule_version ADD applicability_json nvarchar(max) NULL;
IF COL_LENGTH(N'evaluation.rule_version', N'algorithm_sha256') IS NULL
    ALTER TABLE evaluation.rule_version ADD algorithm_sha256 char(64) NULL;
IF COL_LENGTH(N'evaluation.rule_version', N'golden_manifest_sha256') IS NULL
    ALTER TABLE evaluation.rule_version ADD golden_manifest_sha256 char(64) NULL;
IF COL_LENGTH(N'evaluation.rule_version', N'effective_from_utc') IS NULL
    ALTER TABLE evaluation.rule_version ADD effective_from_utc datetime2(3) NULL;
IF COL_LENGTH(N'evaluation.rule_version', N'effective_to_utc') IS NULL
    ALTER TABLE evaluation.rule_version ADD effective_to_utc datetime2(3) NULL;
IF COL_LENGTH(N'evaluation.rule_version', N'supersedes_rule_version_id') IS NULL
    ALTER TABLE evaluation.rule_version ADD supersedes_rule_version_id bigint NULL;
IF COL_LENGTH(N'evaluation.rule_version', N'activation_status') IS NULL
    ALTER TABLE evaluation.rule_version ADD activation_status varchar(16) NOT NULL
        CONSTRAINT DF_evaluation_rule_activation_status DEFAULT('DISABLED') WITH VALUES;
IF COL_LENGTH(N'evaluation.rule_version', N'row_version') IS NULL
    ALTER TABLE evaluation.rule_version ADD row_version rowversion NOT NULL;
GO

IF NOT EXISTS(SELECT 1 FROM sys.foreign_keys WHERE name=N'FK_evaluation_rule_supersedes')
    ALTER TABLE evaluation.rule_version ADD CONSTRAINT FK_evaluation_rule_supersedes
        FOREIGN KEY(supersedes_rule_version_id)
        REFERENCES evaluation.rule_version(evaluation_rule_version_id);
IF NOT EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_evaluation_rule_effective_dates')
    ALTER TABLE evaluation.rule_version ADD CONSTRAINT CK_evaluation_rule_effective_dates
        CHECK(effective_to_utc IS NULL OR effective_from_utc IS NULL
              OR effective_to_utc>effective_from_utc);
IF NOT EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_evaluation_rule_activation_status')
    ALTER TABLE evaluation.rule_version ADD CONSTRAINT CK_evaluation_rule_activation_status
        CHECK(activation_status IN('DISABLED','ENABLED'));
GO

IF OBJECT_ID(N'evaluation.rule_approval_record', N'U') IS NULL
BEGIN
    CREATE TABLE evaluation.rule_approval_record (
        rule_approval_id bigint IDENTITY(1,1) NOT NULL,
        evaluation_rule_version_id bigint NOT NULL,
        approval_role varchar(16) NOT NULL,
        approver_user_id bigint NOT NULL,
        decision varchar(16) NOT NULL,
        decision_note nvarchar(1000) NULL,
        golden_manifest_sha256 char(64) NULL,
        decided_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_rule_approval_decided DEFAULT(SYSUTCDATETIME()),
        CONSTRAINT PK_rule_approval_record PRIMARY KEY CLUSTERED(rule_approval_id),
        CONSTRAINT FK_rule_approval_version FOREIGN KEY(evaluation_rule_version_id)
            REFERENCES evaluation.rule_version(evaluation_rule_version_id),
        CONSTRAINT FK_rule_approval_user FOREIGN KEY(approver_user_id)
            REFERENCES iam.app_user(user_id),
        CONSTRAINT CK_rule_approval_role CHECK(
            approval_role IN('BUSINESS','TECHNICAL','QUALITY')
        ),
        CONSTRAINT CK_rule_approval_decision CHECK(
            decision IN('APPROVED','REJECTED','REVOKED')
        )
    );
END;
GO
IF NOT EXISTS(
    SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'evaluation.rule_approval_record')
      AND name=N'IX_rule_approval_current'
)
    CREATE NONCLUSTERED INDEX IX_rule_approval_current
    ON evaluation.rule_approval_record(
        evaluation_rule_version_id,approval_role,decided_at_utc DESC,rule_approval_id DESC
    ) INCLUDE(decision,approver_user_id,golden_manifest_sha256);
GO

IF OBJECT_ID(N'evaluation.rule_activation', N'U') IS NULL
BEGIN
    CREATE TABLE evaluation.rule_activation (
        rule_activation_id bigint IDENTITY(1,1) NOT NULL,
        evaluation_rule_version_id bigint NOT NULL,
        test_stage varchar(16) NOT NULL,
        supplier_id bigint NULL,
        product_id bigint NULL,
        parameter_pattern nvarchar(300) NULL,
        active bit NOT NULL CONSTRAINT DF_rule_activation_active DEFAULT(0),
        activated_by_user_id bigint NOT NULL,
        activated_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_rule_activation_time DEFAULT(SYSUTCDATETIME()),
        effective_from_utc datetime2(3) NULL,
        effective_to_utc datetime2(3) NULL,
        row_version rowversion NOT NULL,
        CONSTRAINT PK_rule_activation PRIMARY KEY CLUSTERED(rule_activation_id),
        CONSTRAINT FK_rule_activation_version FOREIGN KEY(evaluation_rule_version_id)
            REFERENCES evaluation.rule_version(evaluation_rule_version_id),
        CONSTRAINT FK_rule_activation_supplier FOREIGN KEY(supplier_id)
            REFERENCES mdm.supplier(supplier_id),
        CONSTRAINT FK_rule_activation_product FOREIGN KEY(product_id)
            REFERENCES mdm.product(product_id),
        CONSTRAINT FK_rule_activation_user FOREIGN KEY(activated_by_user_id)
            REFERENCES iam.app_user(user_id),
        CONSTRAINT CK_rule_activation_stage CHECK(test_stage IN('CP','FT')),
        CONSTRAINT CK_rule_activation_dates CHECK(
            effective_to_utc IS NULL OR effective_from_utc IS NULL
            OR effective_to_utc>effective_from_utc
        )
    );
END;
GO
IF NOT EXISTS(
    SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'evaluation.rule_activation')
      AND name=N'IX_rule_activation_scope'
)
    CREATE NONCLUSTERED INDEX IX_rule_activation_scope
    ON evaluation.rule_activation(
        active,test_stage,supplier_id,product_id,evaluation_rule_version_id
    ) INCLUDE(parameter_pattern,effective_from_utc,effective_to_utc);
GO

/* Saved Analysis revisions fix Dataset Version, Filter, Rule and display state. */
IF COL_LENGTH(N'analysis.saved_analysis', N'contract_version') IS NULL
    ALTER TABLE analysis.saved_analysis ADD contract_version nvarchar(64) NOT NULL
        CONSTRAINT DF_saved_analysis_contract DEFAULT('LEGACY_SAVED_ANALYSIS_V1') WITH VALUES;
IF COL_LENGTH(N'analysis.saved_analysis', N'filter_hash') IS NULL
    ALTER TABLE analysis.saved_analysis ADD filter_hash char(64) NULL;
IF COL_LENGTH(N'analysis.saved_analysis', N'context_hash') IS NULL
    ALTER TABLE analysis.saved_analysis ADD context_hash char(64) NULL;
IF COL_LENGTH(N'analysis.saved_analysis', N'current_revision_no') IS NULL
    ALTER TABLE analysis.saved_analysis ADD current_revision_no int NOT NULL
        CONSTRAINT DF_saved_analysis_revision DEFAULT(1) WITH VALUES;
IF COL_LENGTH(N'analysis.saved_analysis', N'lifecycle_status') IS NULL
    ALTER TABLE analysis.saved_analysis ADD lifecycle_status varchar(16) NOT NULL
        CONSTRAINT DF_saved_analysis_lifecycle DEFAULT('ACTIVE') WITH VALUES;
IF COL_LENGTH(N'analysis.saved_analysis', N'row_version') IS NULL
    ALTER TABLE analysis.saved_analysis ADD row_version rowversion NOT NULL;
GO
IF NOT EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_saved_analysis_lifecycle')
    ALTER TABLE analysis.saved_analysis ADD CONSTRAINT CK_saved_analysis_lifecycle
        CHECK(lifecycle_status IN('ACTIVE','DELETED'));
GO

IF OBJECT_ID(N'analysis.saved_analysis_revision', N'U') IS NULL
BEGIN
    CREATE TABLE analysis.saved_analysis_revision (
        saved_analysis_revision_id bigint IDENTITY(1,1) NOT NULL,
        saved_analysis_id bigint NOT NULL,
        revision_no int NOT NULL,
        contract_version nvarchar(64) NOT NULL,
        filter_json nvarchar(max) NOT NULL,
        filter_hash char(64) NOT NULL,
        context_hash char(64) NOT NULL,
        rule_context_json nvarchar(max) NOT NULL,
        chart_config_json nvarchar(max) NOT NULL,
        created_by_user_id bigint NOT NULL,
        created_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_saved_revision_created DEFAULT(SYSUTCDATETIME()),
        CONSTRAINT PK_saved_analysis_revision PRIMARY KEY CLUSTERED(saved_analysis_revision_id),
        CONSTRAINT FK_saved_revision_saved FOREIGN KEY(saved_analysis_id)
            REFERENCES analysis.saved_analysis(saved_analysis_id),
        CONSTRAINT FK_saved_revision_user FOREIGN KEY(created_by_user_id)
            REFERENCES iam.app_user(user_id),
        CONSTRAINT UQ_saved_revision_no UNIQUE(saved_analysis_id,revision_no),
        CONSTRAINT CK_saved_revision_positive CHECK(revision_no>0)
    );
END;
GO

IF OBJECT_ID(N'analysis.saved_analysis_revision_dataset', N'U') IS NULL
BEGIN
    CREATE TABLE analysis.saved_analysis_revision_dataset (
        saved_analysis_revision_id bigint NOT NULL,
        dataset_version_id bigint NOT NULL,
        ordinal_no tinyint NOT NULL,
        CONSTRAINT PK_saved_revision_dataset PRIMARY KEY CLUSTERED(
            saved_analysis_revision_id,dataset_version_id
        ),
        CONSTRAINT FK_saved_revision_dataset_revision FOREIGN KEY(saved_analysis_revision_id)
            REFERENCES analysis.saved_analysis_revision(saved_analysis_revision_id),
        CONSTRAINT FK_saved_revision_dataset_version FOREIGN KEY(dataset_version_id)
            REFERENCES dataset.dataset_version(dataset_version_id),
        CONSTRAINT UQ_saved_revision_dataset_ordinal UNIQUE(
            saved_analysis_revision_id,ordinal_no
        ),
        CONSTRAINT CK_saved_revision_dataset_ordinal CHECK(ordinal_no BETWEEN 1 AND 8)
    );
END;
GO

/* Evaluation and Delivery retain their legacy primary Dataset FK for compatibility. */
IF OBJECT_ID(N'evaluation.evaluation_run_dataset', N'U') IS NULL
BEGIN
    CREATE TABLE evaluation.evaluation_run_dataset (
        evaluation_run_id bigint NOT NULL,
        dataset_version_id bigint NOT NULL,
        ordinal_no tinyint NOT NULL,
        CONSTRAINT PK_evaluation_run_dataset PRIMARY KEY CLUSTERED(
            evaluation_run_id,dataset_version_id
        ),
        CONSTRAINT FK_evaluation_run_dataset_run FOREIGN KEY(evaluation_run_id)
            REFERENCES evaluation.evaluation_run(evaluation_run_id),
        CONSTRAINT FK_evaluation_run_dataset_version FOREIGN KEY(dataset_version_id)
            REFERENCES dataset.dataset_version(dataset_version_id),
        CONSTRAINT UQ_evaluation_run_dataset_ordinal UNIQUE(evaluation_run_id,ordinal_no),
        CONSTRAINT CK_evaluation_run_dataset_ordinal CHECK(ordinal_no BETWEEN 1 AND 8)
    );
END;
GO

IF COL_LENGTH(N'delivery.export_job', N'contract_version') IS NULL
    ALTER TABLE delivery.export_job ADD contract_version nvarchar(64) NOT NULL
        CONSTRAINT DF_export_contract DEFAULT('LEGACY_EXPORT_V1') WITH VALUES;
IF COL_LENGTH(N'delivery.export_job', N'filter_hash') IS NULL
    ALTER TABLE delivery.export_job ADD filter_hash char(64) NULL;
IF COL_LENGTH(N'delivery.export_job', N'context_hash') IS NULL
    ALTER TABLE delivery.export_job ADD context_hash char(64) NULL;
IF COL_LENGTH(N'delivery.export_job', N'rule_context_json') IS NULL
    ALTER TABLE delivery.export_job ADD rule_context_json nvarchar(max) NULL;
IF COL_LENGTH(N'delivery.export_job', N'idempotency_key') IS NULL
    ALTER TABLE delivery.export_job ADD idempotency_key nvarchar(128) NULL;
IF COL_LENGTH(N'delivery.export_job', N'exported_row_count') IS NULL
    ALTER TABLE delivery.export_job ADD exported_row_count bigint NULL;
IF COL_LENGTH(N'delivery.export_job', N'row_version') IS NULL
    ALTER TABLE delivery.export_job ADD row_version rowversion NOT NULL;
GO
IF NOT EXISTS(
    SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'delivery.export_job')
      AND name=N'UX_export_job_idempotency'
)
    CREATE UNIQUE NONCLUSTERED INDEX UX_export_job_idempotency
    ON delivery.export_job(requested_by,idempotency_key)
    WHERE idempotency_key IS NOT NULL;
GO

IF OBJECT_ID(N'delivery.export_job_dataset', N'U') IS NULL
BEGIN
    CREATE TABLE delivery.export_job_dataset (
        export_job_id bigint NOT NULL,
        dataset_version_id bigint NOT NULL,
        ordinal_no tinyint NOT NULL,
        CONSTRAINT PK_export_job_dataset PRIMARY KEY CLUSTERED(
            export_job_id,dataset_version_id
        ),
        CONSTRAINT FK_export_job_dataset_job FOREIGN KEY(export_job_id)
            REFERENCES delivery.export_job(export_job_id),
        CONSTRAINT FK_export_job_dataset_version FOREIGN KEY(dataset_version_id)
            REFERENCES dataset.dataset_version(dataset_version_id),
        CONSTRAINT UQ_export_job_dataset_ordinal UNIQUE(export_job_id,ordinal_no),
        CONSTRAINT CK_export_job_dataset_ordinal CHECK(ordinal_no BETWEEN 1 AND 8)
    );
END;
GO

/* Downgrade is intentionally blocked: approval and reproducibility history is audit data. */
