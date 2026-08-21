SET XACT_ABORT ON;

IF SCHEMA_ID('iam') IS NULL EXEC('CREATE SCHEMA iam AUTHORIZATION dbo');
IF SCHEMA_ID('dataset') IS NULL EXEC('CREATE SCHEMA dataset AUTHORIZATION dbo');
IF SCHEMA_ID('evaluation') IS NULL EXEC('CREATE SCHEMA evaluation AUTHORIZATION dbo');
IF SCHEMA_ID('analysis') IS NULL EXEC('CREATE SCHEMA analysis AUTHORIZATION dbo');
IF SCHEMA_ID('delivery') IS NULL EXEC('CREATE SCHEMA delivery AUTHORIZATION dbo');
GO

CREATE TABLE iam.app_user (
    user_id bigint IDENTITY(1,1) NOT NULL,
    login_name nvarchar(128) NOT NULL,
    display_name nvarchar(200) NOT NULL,
    identity_provider varchar(16) NOT NULL CONSTRAINT DF_app_user_idp DEFAULT('LOCAL'),
    external_subject nvarchar(256) NULL,
    password_hash nvarchar(500) NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_app_user_status DEFAULT('PENDING'),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_app_user_created DEFAULT(SYSUTCDATETIME()),
    updated_at_utc datetime2(3) NOT NULL CONSTRAINT DF_app_user_updated DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_app_user PRIMARY KEY CLUSTERED(user_id),
    CONSTRAINT UQ_app_user_login UNIQUE(login_name),
    CONSTRAINT CK_app_user_idp CHECK(identity_provider IN('LOCAL','AD','LDAP','OIDC')),
    CONSTRAINT CK_app_user_status CHECK(status IN('PENDING','ACTIVE','DISABLED')),
    CONSTRAINT CK_app_user_local_password CHECK(
        (identity_provider='LOCAL' AND password_hash IS NOT NULL)
        OR (identity_provider<>'LOCAL' AND password_hash IS NULL)
    )
);
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_app_user_external_subject
ON iam.app_user(identity_provider,external_subject) WHERE external_subject IS NOT NULL;
GO

CREATE TABLE iam.role (
    role_id bigint IDENTITY(1,1) NOT NULL,
    role_code varchar(64) NOT NULL,
    role_name nvarchar(200) NOT NULL,
    active bit NOT NULL CONSTRAINT DF_role_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_role_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_role PRIMARY KEY CLUSTERED(role_id),
    CONSTRAINT UQ_role_code UNIQUE(role_code)
);
GO

CREATE TABLE iam.permission (
    permission_id bigint IDENTITY(1,1) NOT NULL,
    permission_code varchar(128) NOT NULL,
    description nvarchar(500) NULL,
    CONSTRAINT PK_permission PRIMARY KEY CLUSTERED(permission_id),
    CONSTRAINT UQ_permission_code UNIQUE(permission_code)
);
GO

CREATE TABLE iam.user_role (
    user_id bigint NOT NULL,
    role_id bigint NOT NULL,
    granted_by bigint NULL,
    granted_at_utc datetime2(3) NOT NULL CONSTRAINT DF_user_role_granted DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_user_role PRIMARY KEY CLUSTERED(user_id,role_id),
    CONSTRAINT FK_user_role_user FOREIGN KEY(user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_user_role_role FOREIGN KEY(role_id) REFERENCES iam.role(role_id),
    CONSTRAINT FK_user_role_granter FOREIGN KEY(granted_by) REFERENCES iam.app_user(user_id)
);
GO

CREATE TABLE iam.role_permission (
    role_id bigint NOT NULL,
    permission_id bigint NOT NULL,
    CONSTRAINT PK_role_permission PRIMARY KEY CLUSTERED(role_id,permission_id),
    CONSTRAINT FK_role_permission_role FOREIGN KEY(role_id) REFERENCES iam.role(role_id),
    CONSTRAINT FK_role_permission_permission FOREIGN KEY(permission_id) REFERENCES iam.permission(permission_id)
);
GO

CREATE TABLE iam.data_scope_grant (
    data_scope_grant_id bigint IDENTITY(1,1) NOT NULL,
    user_id bigint NULL,
    role_id bigint NULL,
    scope_type varchar(32) NOT NULL,
    scope_key nvarchar(256) NOT NULL,
    permission_mode varchar(16) NOT NULL CONSTRAINT DF_scope_mode DEFAULT('READ'),
    granted_by bigint NULL,
    granted_at_utc datetime2(3) NOT NULL CONSTRAINT DF_scope_granted DEFAULT(SYSUTCDATETIME()),
    expires_at_utc datetime2(3) NULL,
    CONSTRAINT PK_data_scope_grant PRIMARY KEY CLUSTERED(data_scope_grant_id),
    CONSTRAINT FK_scope_user FOREIGN KEY(user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_scope_role FOREIGN KEY(role_id) REFERENCES iam.role(role_id),
    CONSTRAINT FK_scope_granter FOREIGN KEY(granted_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_scope_subject CHECK((user_id IS NOT NULL AND role_id IS NULL) OR (user_id IS NULL AND role_id IS NOT NULL)),
    CONSTRAINT CK_scope_type CHECK(scope_type IN('GLOBAL','DEPARTMENT','PROJECT','PRODUCT','SUPPLIER','OWNER')),
    CONSTRAINT CK_scope_mode CHECK(permission_mode IN('READ','WRITE','GOVERN','EXPORT'))
);
GO

CREATE TABLE ingestion.import_batch_file (
    import_batch_file_id bigint IDENTITY(1,1) NOT NULL,
    import_batch_id bigint NOT NULL,
    receipt_id bigint NOT NULL,
    file_role varchar(32) NOT NULL,
    ordinal_no int NOT NULL,
    required_flag bit NOT NULL CONSTRAINT DF_import_batch_file_required DEFAULT(1),
    detected_format_code nvarchar(128) NULL,
    detected_profile_version nvarchar(64) NULL,
    detection_evidence_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_import_batch_file_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_import_batch_file PRIMARY KEY CLUSTERED(import_batch_file_id),
    CONSTRAINT FK_import_batch_file_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_import_batch_file_receipt FOREIGN KEY(receipt_id) REFERENCES ingestion.source_file_receipt(receipt_id),
    CONSTRAINT UQ_import_batch_file_ordinal UNIQUE(import_batch_id,ordinal_no),
    CONSTRAINT UQ_import_batch_file_role UNIQUE(import_batch_id,receipt_id,file_role),
    CONSTRAINT CK_import_batch_file_role CHECK(file_role IN('DETAIL','YIELD','SPEC','PAT','EXPORT','REPORT','MANIFEST','OTHER'))
);
GO

CREATE TABLE ingestion.format_profile (
    format_profile_id bigint IDENTITY(1,1) NOT NULL,
    supplier_id bigint NULL,
    test_stage varchar(16) NOT NULL,
    format_code nvarchar(128) NOT NULL,
    profile_version nvarchar(64) NOT NULL,
    signature_json nvarchar(max) NOT NULL,
    file_role_contract_json nvarchar(max) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_format_profile_status DEFAULT('DRAFT'),
    approved_by bigint NULL,
    approved_at_utc datetime2(3) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_format_profile_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_format_profile PRIMARY KEY CLUSTERED(format_profile_id),
    CONSTRAINT FK_format_profile_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT FK_format_profile_approver FOREIGN KEY(approved_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT UQ_format_profile_version UNIQUE(format_code,profile_version),
    CONSTRAINT CK_format_profile_stage CHECK(test_stage IN('CP','FT','WAT','SLT','QA','ORT','SUMMARY','OTHER')),
    CONSTRAINT CK_format_profile_status CHECK(status IN('DRAFT','RELEASED','OBSOLETE'))
);
GO

CREATE TABLE ingestion.cleaner_release (
    cleaner_release_id bigint IDENTITY(1,1) NOT NULL,
    format_profile_id bigint NOT NULL,
    cleaner_code nvarchar(128) NOT NULL,
    cleaner_version nvarchar(64) NOT NULL,
    code_checksum char(64) NOT NULL,
    artifact_uri nvarchar(1000) NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_cleaner_release_status DEFAULT('DRAFT'),
    approved_by bigint NULL,
    approved_at_utc datetime2(3) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_cleaner_release_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_cleaner_release PRIMARY KEY CLUSTERED(cleaner_release_id),
    CONSTRAINT FK_cleaner_profile FOREIGN KEY(format_profile_id) REFERENCES ingestion.format_profile(format_profile_id),
    CONSTRAINT FK_cleaner_approver FOREIGN KEY(approved_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT UQ_cleaner_release UNIQUE(cleaner_code,cleaner_version,format_profile_id),
    CONSTRAINT CK_cleaner_release_status CHECK(status IN('DRAFT','RELEASED','OBSOLETE'))
);
GO

ALTER TABLE ingestion.processing_job ALTER COLUMN source_file_id bigint NULL;
ALTER TABLE ingestion.processing_job ADD import_batch_id bigint NULL;
ALTER TABLE ingestion.processing_job ADD cleaner_release_id bigint NULL;
GO

ALTER TABLE ingestion.processing_job ADD CONSTRAINT FK_job_import_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id);
ALTER TABLE ingestion.processing_job ADD CONSTRAINT FK_job_cleaner_release FOREIGN KEY(cleaner_release_id) REFERENCES ingestion.cleaner_release(cleaner_release_id);
ALTER TABLE ingestion.processing_job ADD CONSTRAINT CK_job_input CHECK(source_file_id IS NOT NULL OR import_batch_id IS NOT NULL);
GO

CREATE TABLE ingestion.dq_rule_set (
    dq_rule_set_id bigint IDENTITY(1,1) NOT NULL,
    rule_set_code varchar(128) NOT NULL,
    version_code nvarchar(64) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_dq_rule_set_status DEFAULT('DRAFT'),
    approved_by bigint NULL,
    approved_at_utc datetime2(3) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_dq_rule_set_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_dq_rule_set PRIMARY KEY CLUSTERED(dq_rule_set_id),
    CONSTRAINT FK_dq_rule_set_approver FOREIGN KEY(approved_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT UQ_dq_rule_set_version UNIQUE(rule_set_code,version_code),
    CONSTRAINT CK_dq_rule_set_status CHECK(status IN('DRAFT','RELEASED','OBSOLETE'))
);
GO

CREATE TABLE ingestion.dq_rule_version (
    dq_rule_version_id bigint IDENTITY(1,1) NOT NULL,
    dq_rule_set_id bigint NOT NULL,
    rule_id bigint NOT NULL,
    implementation_version nvarchar(64) NOT NULL,
    severity varchar(16) NOT NULL,
    is_blocking bit NOT NULL,
    waivable bit NOT NULL,
    parameters_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_dq_rule_version_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_dq_rule_version PRIMARY KEY CLUSTERED(dq_rule_version_id),
    CONSTRAINT FK_dq_rule_version_set FOREIGN KEY(dq_rule_set_id) REFERENCES ingestion.dq_rule_set(dq_rule_set_id),
    CONSTRAINT FK_dq_rule_version_rule FOREIGN KEY(rule_id) REFERENCES ingestion.data_quality_rule(rule_id),
    CONSTRAINT UQ_dq_rule_version UNIQUE(dq_rule_set_id,rule_id),
    CONSTRAINT CK_dq_rule_version_severity CHECK(severity IN('INFO','WARNING','ERROR','BLOCKER')),
    CONSTRAINT CK_dq_rule_version_blocker CHECK(severity<>'BLOCKER' OR (is_blocking=1 AND waivable=0))
);
GO

ALTER TABLE ingestion.data_quality_issue ADD dq_rule_version_id bigint NULL;
ALTER TABLE ingestion.data_quality_issue ADD CONSTRAINT FK_dq_issue_rule_version FOREIGN KEY(dq_rule_version_id) REFERENCES ingestion.dq_rule_version(dq_rule_version_id);
GO

CREATE TABLE dataset.dataset (
    dataset_id bigint IDENTITY(1,1) NOT NULL,
    dataset_code nvarchar(128) NOT NULL,
    dataset_name nvarchar(300) NOT NULL,
    dataset_type varchar(32) NOT NULL,
    test_stage varchar(16) NOT NULL,
    supplier_id bigint NULL,
    product_id bigint NULL,
    project_code nvarchar(128) NULL,
    owner_user_id bigint NOT NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_dataset_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_dataset PRIMARY KEY CLUSTERED(dataset_id),
    CONSTRAINT UQ_dataset_code UNIQUE(dataset_code),
    CONSTRAINT FK_dataset_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT FK_dataset_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT FK_dataset_owner FOREIGN KEY(owner_user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_dataset_type CHECK(dataset_type IN('CP_DETAIL','FT_DETAIL','WAFER_SUMMARY','YIELD_REPORT','OTHER')),
    CONSTRAINT CK_dataset_stage CHECK(test_stage IN('CP','FT','OTHER'))
);
GO

CREATE TABLE dataset.dataset_version (
    dataset_version_id bigint IDENTITY(1,1) NOT NULL,
    dataset_id bigint NOT NULL,
    version_no int NOT NULL,
    input_batch_id bigint NOT NULL,
    canonical_model_version nvarchar(32) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_dataset_version_status DEFAULT('DRAFT'),
    is_current bit NOT NULL CONSTRAINT DF_dataset_version_current DEFAULT(0),
    row_count bigint NULL,
    unit_count bigint NULL,
    measurement_count bigint NULL,
    published_by bigint NULL,
    published_at_utc datetime2(3) NULL,
    supersedes_dataset_version_id bigint NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_dataset_version_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_dataset_version PRIMARY KEY CLUSTERED(dataset_version_id),
    CONSTRAINT FK_dataset_version_dataset FOREIGN KEY(dataset_id) REFERENCES dataset.dataset(dataset_id),
    CONSTRAINT FK_dataset_version_batch FOREIGN KEY(input_batch_id) REFERENCES ingestion.import_batch(import_batch_id),
    CONSTRAINT FK_dataset_version_publisher FOREIGN KEY(published_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_dataset_version_supersedes FOREIGN KEY(supersedes_dataset_version_id) REFERENCES dataset.dataset_version(dataset_version_id),
    CONSTRAINT UQ_dataset_version_no UNIQUE(dataset_id,version_no),
    CONSTRAINT CK_dataset_version_status CHECK(status IN('DRAFT','VALIDATING','PUBLISHED','SUPERSEDED','ARCHIVED')),
    CONSTRAINT CK_dataset_version_publish CHECK(status<>'PUBLISHED' OR (published_by IS NOT NULL AND published_at_utc IS NOT NULL))
);
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_dataset_version_current
ON dataset.dataset_version(dataset_id) WHERE status='PUBLISHED' AND is_current=1;
GO

CREATE TABLE dataset.dataset_version_run (
    dataset_version_id bigint NOT NULL,
    processing_run_id bigint NOT NULL,
    run_role varchar(32) NOT NULL CONSTRAINT DF_dataset_version_run_role DEFAULT('PRIMARY'),
    ordinal_no int NOT NULL CONSTRAINT DF_dataset_version_run_ordinal DEFAULT(1),
    CONSTRAINT PK_dataset_version_run PRIMARY KEY CLUSTERED(dataset_version_id,processing_run_id),
    CONSTRAINT FK_dataset_version_run_version FOREIGN KEY(dataset_version_id) REFERENCES dataset.dataset_version(dataset_version_id),
    CONSTRAINT FK_dataset_version_run_processing FOREIGN KEY(processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT UQ_dataset_version_run_ordinal UNIQUE(dataset_version_id,ordinal_no),
    CONSTRAINT CK_dataset_version_run_role CHECK(run_role IN('PRIMARY','DETAIL','SPEC','YIELD','SUPPORTING'))
);
GO

CREATE TABLE evaluation.rule_set (
    evaluation_rule_set_id bigint IDENTITY(1,1) NOT NULL,
    rule_code varchar(128) NOT NULL,
    rule_name nvarchar(300) NOT NULL,
    evaluation_type varchar(32) NOT NULL,
    owner_name nvarchar(200) NULL,
    CONSTRAINT PK_evaluation_rule_set PRIMARY KEY CLUSTERED(evaluation_rule_set_id),
    CONSTRAINT UQ_evaluation_rule_code UNIQUE(rule_code),
    CONSTRAINT CK_evaluation_rule_type CHECK(evaluation_type IN('SPEC','BIN','PAT','SBL','CPK','SPC','OTHER'))
);
GO

CREATE TABLE evaluation.rule_version (
    evaluation_rule_version_id bigint IDENTITY(1,1) NOT NULL,
    evaluation_rule_set_id bigint NOT NULL,
    version_code nvarchar(64) NOT NULL,
    implementation_version nvarchar(64) NOT NULL,
    parameters_json nvarchar(max) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_evaluation_rule_version_status DEFAULT('DRAFT'),
    approved_by bigint NULL,
    approved_at_utc datetime2(3) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_evaluation_rule_version_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_evaluation_rule_version PRIMARY KEY CLUSTERED(evaluation_rule_version_id),
    CONSTRAINT FK_evaluation_rule_version_set FOREIGN KEY(evaluation_rule_set_id) REFERENCES evaluation.rule_set(evaluation_rule_set_id),
    CONSTRAINT FK_evaluation_rule_approver FOREIGN KEY(approved_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT UQ_evaluation_rule_version UNIQUE(evaluation_rule_set_id,version_code),
    CONSTRAINT CK_evaluation_rule_version_status CHECK(status IN('DRAFT','RELEASED','OBSOLETE'))
);
GO

CREATE TABLE evaluation.evaluation_run (
    evaluation_run_id bigint IDENTITY(1,1) NOT NULL,
    dataset_version_id bigint NOT NULL,
    evaluation_rule_version_id bigint NOT NULL,
    evaluation_scope_key nvarchar(200) NOT NULL,
    filter_json nvarchar(max) NOT NULL,
    filter_hash char(64) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_evaluation_run_status DEFAULT('QUEUED'),
    sample_count bigint NULL,
    excluded_count bigint NULL,
    requested_by bigint NOT NULL,
    started_at_utc datetime2(3) NULL,
    finished_at_utc datetime2(3) NULL,
    error_message nvarchar(max) NULL,
    CONSTRAINT PK_evaluation_run PRIMARY KEY CLUSTERED(evaluation_run_id),
    CONSTRAINT FK_evaluation_run_dataset FOREIGN KEY(dataset_version_id) REFERENCES dataset.dataset_version(dataset_version_id),
    CONSTRAINT FK_evaluation_run_rule FOREIGN KEY(evaluation_rule_version_id) REFERENCES evaluation.rule_version(evaluation_rule_version_id),
    CONSTRAINT FK_evaluation_run_requester FOREIGN KEY(requested_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_evaluation_run_status CHECK(status IN('QUEUED','RUNNING','SUCCESS','FAILED','CANCELLED'))
);
GO

CREATE NONCLUSTERED INDEX IX_evaluation_run_lookup
ON evaluation.evaluation_run(dataset_version_id,evaluation_rule_version_id,filter_hash,status);
GO

CREATE TABLE evaluation.metric_result (
    metric_result_id bigint IDENTITY(1,1) NOT NULL,
    evaluation_run_id bigint NOT NULL,
    metric_code varchar(64) NOT NULL,
    scope_key nvarchar(300) NOT NULL,
    result_json nvarchar(max) NOT NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_metric_result_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_metric_result PRIMARY KEY CLUSTERED(metric_result_id),
    CONSTRAINT FK_metric_result_run FOREIGN KEY(evaluation_run_id) REFERENCES evaluation.evaluation_run(evaluation_run_id),
    CONSTRAINT UQ_metric_result UNIQUE(evaluation_run_id,metric_code,scope_key)
);
GO

ALTER TABLE test.measurement_evaluation ADD evaluation_run_id bigint NULL;
ALTER TABLE test.measurement_evaluation ADD CONSTRAINT FK_measurement_eval_run FOREIGN KEY(evaluation_run_id) REFERENCES evaluation.evaluation_run(evaluation_run_id);
GO

CREATE TABLE analysis.saved_analysis (
    saved_analysis_id bigint IDENTITY(1,1) NOT NULL,
    owner_user_id bigint NOT NULL,
    dataset_version_id bigint NOT NULL,
    analysis_name nvarchar(300) NOT NULL,
    filter_json nvarchar(max) NOT NULL,
    chart_config_json nvarchar(max) NOT NULL,
    evaluation_context_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_saved_analysis_created DEFAULT(SYSUTCDATETIME()),
    updated_at_utc datetime2(3) NOT NULL CONSTRAINT DF_saved_analysis_updated DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_saved_analysis PRIMARY KEY CLUSTERED(saved_analysis_id),
    CONSTRAINT FK_saved_analysis_owner FOREIGN KEY(owner_user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_saved_analysis_dataset FOREIGN KEY(dataset_version_id) REFERENCES dataset.dataset_version(dataset_version_id)
);
GO

CREATE TABLE delivery.export_job (
    export_job_id bigint IDENTITY(1,1) NOT NULL,
    requested_by bigint NOT NULL,
    dataset_version_id bigint NOT NULL,
    evaluation_run_id bigint NULL,
    export_scope varchar(16) NOT NULL,
    export_format varchar(16) NOT NULL,
    template_code nvarchar(128) NOT NULL,
    template_version nvarchar(64) NOT NULL,
    filter_json nvarchar(max) NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_export_job_status DEFAULT('QUEUED'),
    requested_at_utc datetime2(3) NOT NULL CONSTRAINT DF_export_job_requested DEFAULT(SYSUTCDATETIME()),
    started_at_utc datetime2(3) NULL,
    finished_at_utc datetime2(3) NULL,
    error_message nvarchar(max) NULL,
    CONSTRAINT PK_export_job PRIMARY KEY CLUSTERED(export_job_id),
    CONSTRAINT FK_export_job_user FOREIGN KEY(requested_by) REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_export_job_dataset FOREIGN KEY(dataset_version_id) REFERENCES dataset.dataset_version(dataset_version_id),
    CONSTRAINT FK_export_job_evaluation FOREIGN KEY(evaluation_run_id) REFERENCES evaluation.evaluation_run(evaluation_run_id),
    CONSTRAINT CK_export_job_scope CHECK(export_scope IN('CURRENT_PAGE','FILTERED_RESULT','FULL_DATASET','REPORT')),
    CONSTRAINT CK_export_job_format CHECK(export_format IN('CSV','XLSX','PNG','HTML','PDF','BIN_TXT')),
    CONSTRAINT CK_export_job_status CHECK(status IN('QUEUED','RUNNING','SUCCESS','FAILED','CANCELLED','EXPIRED'))
);
GO

CREATE TABLE delivery.export_artifact (
    export_artifact_id bigint IDENTITY(1,1) NOT NULL,
    export_job_id bigint NOT NULL,
    file_name nvarchar(500) NOT NULL,
    mime_type nvarchar(128) NOT NULL,
    storage_uri nvarchar(1000) NOT NULL,
    file_size bigint NOT NULL,
    sha256 char(64) NOT NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_export_artifact_created DEFAULT(SYSUTCDATETIME()),
    expires_at_utc datetime2(3) NULL,
    CONSTRAINT PK_export_artifact PRIMARY KEY CLUSTERED(export_artifact_id),
    CONSTRAINT FK_export_artifact_job FOREIGN KEY(export_job_id) REFERENCES delivery.export_job(export_job_id),
    CONSTRAINT UQ_export_artifact_sha UNIQUE(export_job_id,sha256),
    CONSTRAINT CK_export_artifact_size CHECK(file_size>=0)
);
GO

ALTER TABLE governance.audit_log ADD actor_user_id bigint NULL;
ALTER TABLE governance.audit_log ADD CONSTRAINT FK_audit_actor_user FOREIGN KEY(actor_user_id) REFERENCES iam.app_user(user_id);
GO
