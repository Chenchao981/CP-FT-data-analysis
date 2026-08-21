SET XACT_ABORT ON;

IF SCHEMA_ID('mdm') IS NULL EXEC('CREATE SCHEMA mdm AUTHORIZATION dbo');
IF SCHEMA_ID('ingestion') IS NULL EXEC('CREATE SCHEMA ingestion AUTHORIZATION dbo');
IF SCHEMA_ID('test') IS NULL EXEC('CREATE SCHEMA test AUTHORIZATION dbo');
IF SCHEMA_ID('trace') IS NULL EXEC('CREATE SCHEMA trace AUTHORIZATION dbo');
IF SCHEMA_ID('analytics') IS NULL EXEC('CREATE SCHEMA analytics AUTHORIZATION dbo');
IF SCHEMA_ID('governance') IS NULL EXEC('CREATE SCHEMA governance AUTHORIZATION dbo');
GO

CREATE TABLE mdm.supplier (
    supplier_id bigint IDENTITY(1,1) NOT NULL,
    supplier_code nvarchar(64) NOT NULL,
    supplier_name nvarchar(200) NOT NULL,
    supplier_type varchar(32) NULL,
    default_timezone_iana nvarchar(64) NULL,
    active bit NOT NULL CONSTRAINT DF_supplier_active DEFAULT(1),
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_supplier_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_supplier PRIMARY KEY CLUSTERED(supplier_id),
    CONSTRAINT UQ_supplier_code UNIQUE(supplier_code)
);
GO

CREATE TABLE mdm.product (
    product_id bigint IDENTITY(1,1) NOT NULL,
    product_code nvarchar(128) NOT NULL,
    product_name nvarchar(200) NULL,
    package_code nvarchar(64) NULL,
    family nvarchar(128) NULL,
    active bit NOT NULL CONSTRAINT DF_product_active DEFAULT(1),
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_product_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_product PRIMARY KEY CLUSTERED(product_id),
    CONSTRAINT UQ_product_code UNIQUE(product_code)
);
GO

CREATE TABLE mdm.product_alias (
    product_alias_id bigint IDENTITY(1,1) NOT NULL,
    product_id bigint NOT NULL,
    alias_type varchar(32) NOT NULL,
    supplier_id bigint NULL,
    alias_value nvarchar(200) NOT NULL,
    active bit NOT NULL CONSTRAINT DF_product_alias_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_product_alias_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_product_alias PRIMARY KEY CLUSTERED(product_alias_id),
    CONSTRAINT FK_product_alias_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT FK_product_alias_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id)
);
GO
CREATE UNIQUE NONCLUSTERED INDEX UX_product_alias_active
ON mdm.product_alias(alias_type,supplier_id,alias_value) WHERE active=1;
GO

CREATE TABLE mdm.test_program (
    test_program_id bigint IDENTITY(1,1) NOT NULL,
    supplier_id bigint NOT NULL,
    product_id bigint NOT NULL,
    test_stage varchar(16) NOT NULL,
    program_code nvarchar(200) NOT NULL,
    program_name nvarchar(300) NULL,
    active bit NOT NULL CONSTRAINT DF_test_program_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_test_program_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_test_program PRIMARY KEY CLUSTERED(test_program_id),
    CONSTRAINT FK_test_program_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT FK_test_program_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT CK_test_program_stage CHECK(test_stage IN('CP','FT','WAT','SLT','QA','ORT','OTHER')),
    CONSTRAINT UQ_test_program UNIQUE(supplier_id,product_id,test_stage,program_code)
);
GO

CREATE TABLE mdm.test_program_version (
    program_version_id bigint IDENTITY(1,1) NOT NULL,
    test_program_id bigint NOT NULL,
    version_code nvarchar(128) NOT NULL,
    raw_program_name nvarchar(500) NULL,
    program_checksum char(64) NULL,
    effective_from_utc datetime2(3) NULL,
    effective_to_utc datetime2(3) NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_program_version_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_program_version PRIMARY KEY CLUSTERED(program_version_id),
    CONSTRAINT FK_program_version_program FOREIGN KEY(test_program_id) REFERENCES mdm.test_program(test_program_id),
    CONSTRAINT UQ_program_version UNIQUE(test_program_id,version_code),
    CONSTRAINT CK_program_version_dates CHECK(effective_to_utc IS NULL OR effective_from_utc IS NULL OR effective_to_utc > effective_from_utc)
);
GO

CREATE TABLE mdm.test_item_definition (
    test_item_id bigint IDENTITY(1,1) NOT NULL,
    program_version_id bigint NOT NULL,
    sequence_no int NOT NULL,
    step_code nvarchar(64) NOT NULL,
    raw_item_name nvarchar(200) NULL,
    canonical_parameter_code nvarchar(128) NULL,
    display_name nvarchar(300) NULL,
    data_type varchar(16) NOT NULL CONSTRAINT DF_test_item_type DEFAULT('NUMERIC'),
    unit_raw nvarchar(64) NULL,
    unit_code nvarchar(32) NULL,
    program_lsl float(53) NULL,
    program_usl float(53) NULL,
    lower_operator nvarchar(8) NULL,
    upper_operator nvarchar(8) NULL,
    lower_limit_raw nvarchar(128) NULL,
    upper_limit_raw nvarchar(128) NULL,
    condition_json nvarchar(max) NULL,
    source_column_index int NULL,
    is_analysis_parameter bit NOT NULL CONSTRAINT DF_test_item_analysis DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_test_item_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_test_item PRIMARY KEY CLUSTERED(test_item_id),
    CONSTRAINT FK_test_item_program_version FOREIGN KEY(program_version_id) REFERENCES mdm.test_program_version(program_version_id),
    CONSTRAINT UQ_test_item_step UNIQUE(program_version_id,step_code),
    CONSTRAINT UQ_test_item_sequence UNIQUE(program_version_id,sequence_no),
    CONSTRAINT CK_test_item_type CHECK(data_type IN('NUMERIC','TEXT','BOOLEAN'))
);
GO

CREATE TABLE mdm.scope_priority (
    scope_code varchar(64) NOT NULL,
    priority int NOT NULL,
    description nvarchar(300) NULL,
    active bit NOT NULL CONSTRAINT DF_scope_priority_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_scope_priority_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_scope_priority PRIMARY KEY CLUSTERED(scope_code),
    CONSTRAINT UQ_scope_priority_value UNIQUE(priority)
);
GO

CREATE TABLE mdm.spec_set (
    spec_set_id bigint IDENTITY(1,1) NOT NULL,
    product_id bigint NULL,
    test_stage varchar(16) NULL,
    spec_name nvarchar(200) NOT NULL,
    version_code nvarchar(128) NOT NULL,
    status varchar(32) NOT NULL CONSTRAINT DF_spec_status DEFAULT('DRAFT'),
    source_type varchar(32) NULL,
    source_ref nvarchar(500) NULL,
    effective_from_utc datetime2(3) NULL,
    effective_to_utc datetime2(3) NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_spec_set_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_spec_set PRIMARY KEY CLUSTERED(spec_set_id),
    CONSTRAINT FK_spec_set_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT CK_spec_status CHECK(status IN('DRAFT','RELEASED','OBSOLETE')),
    CONSTRAINT CK_spec_stage CHECK(test_stage IS NULL OR test_stage IN('CP','FT','WAT','SLT','QA','ORT','OTHER')),
    CONSTRAINT CK_spec_dates CHECK(effective_to_utc IS NULL OR effective_from_utc IS NULL OR effective_to_utc > effective_from_utc)
);
GO

CREATE TABLE mdm.spec_item (
    spec_item_id bigint IDENTITY(1,1) NOT NULL,
    spec_set_id bigint NOT NULL,
    test_item_id bigint NULL,
    canonical_parameter_code nvarchar(128) NOT NULL,
    lsl float(53) NULL,
    usl float(53) NULL,
    target_value float(53) NULL,
    lower_operator nvarchar(8) NULL,
    upper_operator nvarchar(8) NULL,
    unit_code nvarchar(32) NULL,
    raw_spec nvarchar(500) NULL,
    condition_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_spec_item_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_spec_item PRIMARY KEY CLUSTERED(spec_item_id),
    CONSTRAINT FK_spec_item_set FOREIGN KEY(spec_set_id) REFERENCES mdm.spec_set(spec_set_id),
    CONSTRAINT FK_spec_item_test_item FOREIGN KEY(test_item_id) REFERENCES mdm.test_item_definition(test_item_id)
);
GO
CREATE NONCLUSTERED INDEX IX_spec_item_lookup
ON mdm.spec_item(spec_set_id,canonical_parameter_code) INCLUDE(test_item_id,lsl,usl,unit_code);
GO

CREATE TABLE mdm.spec_binding (
    spec_binding_id bigint IDENTITY(1,1) NOT NULL,
    spec_set_id bigint NOT NULL,
    scope_code varchar(64) NOT NULL,
    supplier_id bigint NULL,
    product_id bigint NULL,
    test_stage varchar(16) NULL,
    program_version_id bigint NULL,
    customer_code nvarchar(128) NULL,
    quality_grade nvarchar(64) NULL,
    package_code nvarchar(64) NULL,
    effective_from_utc datetime2(3) NULL,
    effective_to_utc datetime2(3) NULL,
    active bit NOT NULL CONSTRAINT DF_spec_binding_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_spec_binding_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_spec_binding PRIMARY KEY CLUSTERED(spec_binding_id),
    CONSTRAINT FK_spec_binding_set FOREIGN KEY(spec_set_id) REFERENCES mdm.spec_set(spec_set_id),
    CONSTRAINT FK_spec_binding_scope FOREIGN KEY(scope_code) REFERENCES mdm.scope_priority(scope_code),
    CONSTRAINT FK_spec_binding_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT FK_spec_binding_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT FK_spec_binding_program FOREIGN KEY(program_version_id) REFERENCES mdm.test_program_version(program_version_id),
    CONSTRAINT CK_spec_binding_stage CHECK(test_stage IS NULL OR test_stage IN('CP','FT','WAT','SLT','QA','ORT','OTHER')),
    CONSTRAINT CK_spec_binding_dates CHECK(effective_to_utc IS NULL OR effective_from_utc IS NULL OR effective_to_utc > effective_from_utc)
);
GO
CREATE NONCLUSTERED INDEX IX_spec_binding_resolve
ON mdm.spec_binding(active,product_id,test_stage,program_version_id,supplier_id,scope_code)
INCLUDE(spec_set_id,customer_code,quality_grade,package_code,effective_from_utc,effective_to_utc);
GO

CREATE TABLE mdm.bin_mapping_set (
    bin_mapping_set_id bigint IDENTITY(1,1) NOT NULL,
    mapping_name nvarchar(200) NOT NULL,
    version_code nvarchar(128) NOT NULL,
    scope_code varchar(64) NOT NULL,
    supplier_id bigint NULL,
    product_id bigint NULL,
    test_stage varchar(16) NULL,
    program_version_id bigint NULL,
    effective_from_utc datetime2(3) NULL,
    effective_to_utc datetime2(3) NULL,
    active bit NOT NULL CONSTRAINT DF_bin_mapping_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_bin_mapping_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_bin_mapping_set PRIMARY KEY CLUSTERED(bin_mapping_set_id),
    CONSTRAINT FK_bin_mapping_scope FOREIGN KEY(scope_code) REFERENCES mdm.scope_priority(scope_code),
    CONSTRAINT FK_bin_mapping_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT FK_bin_mapping_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT FK_bin_mapping_program FOREIGN KEY(program_version_id) REFERENCES mdm.test_program_version(program_version_id),
    CONSTRAINT CK_bin_mapping_stage CHECK(test_stage IS NULL OR test_stage IN('CP','FT','WAT','SLT','QA','ORT','OTHER')),
    CONSTRAINT CK_bin_mapping_dates CHECK(effective_to_utc IS NULL OR effective_from_utc IS NULL OR effective_to_utc > effective_from_utc)
);
GO

CREATE TABLE mdm.bin_definition (
    bin_definition_id bigint IDENTITY(1,1) NOT NULL,
    bin_mapping_set_id bigint NOT NULL,
    bin_type varchar(16) NOT NULL,
    bin_code nvarchar(32) NOT NULL,
    bin_name nvarchar(200) NULL,
    failure_mode nvarchar(300) NULL,
    is_pass bit NOT NULL CONSTRAINT DF_bin_definition_pass DEFAULT(0),
    severity int NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_bin_definition_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_bin_definition PRIMARY KEY CLUSTERED(bin_definition_id),
    CONSTRAINT FK_bin_definition_set FOREIGN KEY(bin_mapping_set_id) REFERENCES mdm.bin_mapping_set(bin_mapping_set_id),
    CONSTRAINT UQ_bin_definition UNIQUE(bin_mapping_set_id,bin_type,bin_code),
    CONSTRAINT CK_bin_type CHECK(bin_type IN('CP_BIN','SOFT_BIN','HARD_BIN','OTHER'))
);
GO

CREATE TABLE ingestion.import_batch (
    import_batch_id bigint IDENTITY(1,1) NOT NULL,
    source_channel varchar(32) NOT NULL CONSTRAINT DF_import_channel DEFAULT('MANUAL'),
    uploaded_by nvarchar(128) NULL,
    status varchar(32) NOT NULL CONSTRAINT DF_import_status DEFAULT('RECEIVED'),
    started_at_utc datetime2(3) NOT NULL CONSTRAINT DF_import_started DEFAULT(SYSUTCDATETIME()),
    completed_at_utc datetime2(3) NULL,
    metadata_json nvarchar(max) NULL,
    CONSTRAINT PK_import_batch PRIMARY KEY CLUSTERED(import_batch_id)
);
GO

CREATE TABLE ingestion.source_file (
    source_file_id bigint IDENTITY(1,1) NOT NULL,
    sha256 char(64) NULL,
    file_size bigint NULL,
    canonical_storage_uri nvarchar(1000) NULL,
    first_seen_utc datetime2(3) NOT NULL CONSTRAINT DF_source_file_seen DEFAULT(SYSUTCDATETIME()),
    metadata_json nvarchar(max) NULL,
    CONSTRAINT PK_source_file PRIMARY KEY CLUSTERED(source_file_id)
);
GO
CREATE UNIQUE NONCLUSTERED INDEX UX_source_file_sha256 ON ingestion.source_file(sha256) WHERE sha256 IS NOT NULL;
GO

CREATE TABLE ingestion.source_file_receipt (
    receipt_id bigint IDENTITY(1,1) NOT NULL,
    source_file_id bigint NOT NULL,
    import_batch_id bigint NULL,
    original_file_name nvarchar(500) NOT NULL,
    received_by nvarchar(128) NULL,
    received_channel varchar(32) NOT NULL CONSTRAINT DF_receipt_channel DEFAULT('MANUAL'),
    received_at_utc datetime2(3) NOT NULL CONSTRAINT DF_receipt_received DEFAULT(SYSUTCDATETIME()),
    is_duplicate_receipt bit NOT NULL CONSTRAINT DF_receipt_duplicate DEFAULT(0),
    metadata_json nvarchar(max) NULL,
    CONSTRAINT PK_source_file_receipt PRIMARY KEY CLUSTERED(receipt_id),
    CONSTRAINT FK_receipt_file FOREIGN KEY(source_file_id) REFERENCES ingestion.source_file(source_file_id),
    CONSTRAINT FK_receipt_batch FOREIGN KEY(import_batch_id) REFERENCES ingestion.import_batch(import_batch_id)
);
GO

CREATE TABLE ingestion.parser_profile (
    parser_profile_id bigint IDENTITY(1,1) NOT NULL,
    format_code nvarchar(128) NOT NULL,
    supplier_id bigint NULL,
    test_stage varchar(16) NULL,
    parser_name nvarchar(200) NOT NULL,
    parser_version nvarchar(64) NOT NULL,
    code_checksum char(64) NULL,
    canonical_model_version nvarchar(32) NOT NULL,
    detect_rules_json nvarchar(max) NULL,
    active bit NOT NULL CONSTRAINT DF_parser_active DEFAULT(1),
    is_default bit NOT NULL CONSTRAINT DF_parser_default DEFAULT(0),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_parser_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_parser_profile PRIMARY KEY CLUSTERED(parser_profile_id),
    CONSTRAINT FK_parser_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT UQ_parser_version UNIQUE(format_code,parser_version),
    CONSTRAINT CK_parser_stage CHECK(test_stage IS NULL OR test_stage IN('CP','FT','WAT','SLT','QA','ORT','OTHER'))
);
GO
CREATE UNIQUE NONCLUSTERED INDEX UX_parser_default
ON ingestion.parser_profile(format_code) WHERE is_default=1 AND active=1;
GO

CREATE TABLE ingestion.processing_job (
    job_id bigint IDENTITY(1,1) NOT NULL,
    source_file_id bigint NOT NULL,
    job_type varchar(32) NOT NULL,
    trigger_type varchar(32) NOT NULL,
    requested_by nvarchar(128) NULL,
    parent_job_id bigint NULL,
    status varchar(32) NOT NULL CONSTRAINT DF_job_status DEFAULT('QUEUED'),
    reason nvarchar(1000) NULL,
    requested_at_utc datetime2(3) NOT NULL CONSTRAINT DF_job_requested DEFAULT(SYSUTCDATETIME()),
    started_at_utc datetime2(3) NULL,
    finished_at_utc datetime2(3) NULL,
    error_code nvarchar(64) NULL,
    error_message nvarchar(max) NULL,
    metadata_json nvarchar(max) NULL,
    CONSTRAINT PK_processing_job PRIMARY KEY CLUSTERED(job_id),
    CONSTRAINT FK_job_source FOREIGN KEY(source_file_id) REFERENCES ingestion.source_file(source_file_id),
    CONSTRAINT FK_job_parent FOREIGN KEY(parent_job_id) REFERENCES ingestion.processing_job(job_id),
    CONSTRAINT CK_job_type CHECK(job_type IN('PARSE','REPROCESS','REEVALUATE','OTHER')),
    CONSTRAINT CK_job_trigger CHECK(trigger_type IN('MANUAL','AUTO','API','SCHEDULED','SYSTEM')),
    CONSTRAINT CK_job_status CHECK(status IN('QUEUED','RUNNING','SUCCESS','FAILED','CANCELLED'))
);
GO

CREATE TABLE ingestion.processing_run (
    processing_run_id bigint IDENTITY(1,1) NOT NULL,
    job_id bigint NOT NULL,
    source_file_id bigint NOT NULL,
    parser_profile_id bigint NOT NULL,
    parser_version nvarchar(64) NOT NULL,
    canonical_model_version nvarchar(32) NOT NULL,
    status varchar(32) NOT NULL CONSTRAINT DF_processing_run_status DEFAULT('CREATED'),
    is_current bit NOT NULL CONSTRAINT DF_processing_run_current DEFAULT(0),
    supersedes_processing_run_id bigint NULL,
    row_count_input bigint NULL,
    unit_count_output bigint NULL,
    measurement_count_output bigint NULL,
    dq_warning_count int NOT NULL CONSTRAINT DF_processing_run_dq_warn DEFAULT(0),
    dq_error_count int NOT NULL CONSTRAINT DF_processing_run_dq_error DEFAULT(0),
    started_at_utc datetime2(3) NOT NULL CONSTRAINT DF_processing_run_started DEFAULT(SYSUTCDATETIME()),
    finished_at_utc datetime2(3) NULL,
    metadata_json nvarchar(max) NULL,
    CONSTRAINT PK_processing_run PRIMARY KEY CLUSTERED(processing_run_id),
    CONSTRAINT FK_processing_run_job FOREIGN KEY(job_id) REFERENCES ingestion.processing_job(job_id),
    CONSTRAINT FK_processing_run_source FOREIGN KEY(source_file_id) REFERENCES ingestion.source_file(source_file_id),
    CONSTRAINT FK_processing_run_parser FOREIGN KEY(parser_profile_id) REFERENCES ingestion.parser_profile(parser_profile_id),
    CONSTRAINT FK_processing_run_supersedes FOREIGN KEY(supersedes_processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT CK_processing_run_status CHECK(status IN('CREATED','PARSING','NORMALIZING','VALIDATING','READY','PUBLISHED','FAILED','SUPERSEDED','CANCELLED'))
);
GO
CREATE UNIQUE NONCLUSTERED INDEX UX_processing_run_current
ON ingestion.processing_run(source_file_id) WHERE is_current=1 AND status='PUBLISHED';
GO

CREATE TABLE ingestion.data_quality_rule (
    rule_id bigint IDENTITY(1,1) NOT NULL,
    rule_code varchar(128) NOT NULL,
    rule_name nvarchar(300) NOT NULL,
    default_severity varchar(16) NOT NULL,
    is_blocking bit NOT NULL CONSTRAINT DF_dq_rule_blocking DEFAULT(0),
    applies_stage varchar(16) NULL,
    description nvarchar(1000) NULL,
    active bit NOT NULL CONSTRAINT DF_dq_rule_active DEFAULT(1),
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_dq_rule_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_dq_rule PRIMARY KEY CLUSTERED(rule_id),
    CONSTRAINT UQ_dq_rule_code UNIQUE(rule_code),
    CONSTRAINT CK_dq_rule_severity CHECK(default_severity IN('INFO','WARNING','ERROR','BLOCKER'))
);
GO

CREATE TABLE ingestion.data_quality_issue (
    issue_id bigint IDENTITY(1,1) NOT NULL,
    processing_run_id bigint NOT NULL,
    rule_id bigint NOT NULL,
    severity varchar(16) NOT NULL,
    entity_type varchar(64) NULL,
    entity_key nvarchar(300) NULL,
    source_row_no int NULL,
    source_column nvarchar(200) NULL,
    raw_value nvarchar(1000) NULL,
    message nvarchar(2000) NOT NULL,
    resolution_status varchar(32) NOT NULL CONSTRAINT DF_dq_issue_resolution DEFAULT('OPEN'),
    resolved_by nvarchar(128) NULL,
    resolved_at_utc datetime2(3) NULL,
    resolution_note nvarchar(1000) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_dq_issue_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_dq_issue PRIMARY KEY CLUSTERED(issue_id),
    CONSTRAINT FK_dq_issue_run FOREIGN KEY(processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT FK_dq_issue_rule FOREIGN KEY(rule_id) REFERENCES ingestion.data_quality_rule(rule_id),
    CONSTRAINT CK_dq_issue_severity CHECK(severity IN('INFO','WARNING','ERROR','BLOCKER')),
    CONSTRAINT CK_dq_issue_resolution CHECK(resolution_status IN('OPEN','RESOLVED','WAIVED','IGNORED'))
);
GO
CREATE NONCLUSTERED INDEX IX_dq_issue_run ON ingestion.data_quality_issue(processing_run_id,severity,resolution_status);
GO

CREATE TABLE test.test_run (
    run_id bigint IDENTITY(1,1) NOT NULL,
    processing_run_id bigint NOT NULL,
    supplier_id bigint NOT NULL,
    product_id bigint NOT NULL,
    program_version_id bigint NULL,
    test_stage varchar(16) NOT NULL,
    work_order_no nvarchar(128) NULL,
    lot_id nvarchar(128) NOT NULL,
    wafer_id nvarchar(64) NULL,
    run_attempt_no smallint NOT NULL CONSTRAINT DF_test_run_attempt DEFAULT(0),
    started_at_utc datetime2(3) NULL,
    ended_at_utc datetime2(3) NULL,
    source_started_local datetime2(3) NULL,
    source_ended_local datetime2(3) NULL,
    source_timezone_iana nvarchar(64) NULL,
    source_utc_offset_minutes smallint NULL,
    timezone_resolution varchar(32) NOT NULL CONSTRAINT DF_timezone_resolution DEFAULT('UNKNOWN'),
    timestamp_source varchar(32) NOT NULL CONSTRAINT DF_timestamp_source DEFAULT('UNKNOWN'),
    tester_id nvarchar(128) NULL,
    handler_id nvarchar(128) NULL,
    prober_id nvarchar(128) NULL,
    probe_card_id nvarchar(128) NULL,
    operator_name nvarchar(128) NULL,
    temperature_c decimal(8,3) NULL,
    notch_orientation varchar(16) NULL,
    origin_position varchar(32) NULL,
    x_direction varchar(16) NULL,
    y_direction varchar(16) NULL,
    rotation_degree decimal(8,3) NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_test_run_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_test_run PRIMARY KEY CLUSTERED(run_id),
    CONSTRAINT FK_test_run_processing FOREIGN KEY(processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT FK_test_run_supplier FOREIGN KEY(supplier_id) REFERENCES mdm.supplier(supplier_id),
    CONSTRAINT FK_test_run_product FOREIGN KEY(product_id) REFERENCES mdm.product(product_id),
    CONSTRAINT FK_test_run_program FOREIGN KEY(program_version_id) REFERENCES mdm.test_program_version(program_version_id),
    CONSTRAINT CK_test_run_stage CHECK(test_stage IN('CP','FT','WAT','SLT','QA','ORT','OTHER')),
    CONSTRAINT CK_test_run_timezone_resolution CHECK(timezone_resolution IN('SOURCE_EXPLICIT','SUPPLIER_DEFAULT','FILE_RULE','UNKNOWN'))
);
GO
CREATE NONCLUSTERED INDEX IX_test_run_lot_wafer ON test.test_run(lot_id,wafer_id) INCLUDE(product_id,test_stage,started_at_utc,run_attempt_no);
GO

CREATE TABLE test.unit_result (
    unit_id bigint IDENTITY(1,1) NOT NULL,
    run_id bigint NOT NULL,
    logical_unit_key nvarchar(300) NOT NULL,
    attempt_no smallint NOT NULL CONSTRAINT DF_unit_attempt DEFAULT(0),
    unit_sequence bigint NULL,
    vendor_unit_id nvarchar(128) NULL,
    wafer_id nvarchar(64) NULL,
    x_coord int NULL,
    y_coord int NULL,
    site_no smallint NULL,
    serial_no nvarchar(128) NULL,
    soft_bin nvarchar(32) NULL,
    hard_bin nvarchar(32) NULL,
    overall_result varchar(16) NOT NULL CONSTRAINT DF_unit_result DEFAULT('UNKNOWN'),
    fail_test_no nvarchar(64) NULL,
    fail_test_name nvarchar(200) NULL,
    test_duration_ms bigint NULL,
    source_row_no int NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_unit_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_unit_result PRIMARY KEY CLUSTERED(unit_id),
    CONSTRAINT FK_unit_run FOREIGN KEY(run_id) REFERENCES test.test_run(run_id),
    CONSTRAINT UQ_unit_attempt UNIQUE(run_id,logical_unit_key,attempt_no),
    CONSTRAINT CK_unit_overall_result CHECK(overall_result IN('PASS','FAIL','ABORT','UNKNOWN'))
);
GO
CREATE NONCLUSTERED INDEX IX_unit_logical ON test.unit_result(logical_unit_key,attempt_no) INCLUDE(run_id,overall_result,soft_bin,hard_bin);
GO
CREATE NONCLUSTERED INDEX IX_unit_xy ON test.unit_result(run_id,x_coord,y_coord,attempt_no) WHERE x_coord IS NOT NULL AND y_coord IS NOT NULL;
GO

CREATE TABLE test.measurement (
    measurement_id bigint IDENTITY(1,1) NOT NULL,
    unit_id bigint NOT NULL,
    test_item_id bigint NOT NULL,
    value_numeric float(53) NULL,
    value_text nvarchar(256) NULL,
    raw_value nvarchar(256) NULL,
    measurement_status varchar(24) NOT NULL,
    tester_pass_flag bit NULL,
    source_column_index int NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_measurement_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_measurement PRIMARY KEY CLUSTERED(measurement_id),
    CONSTRAINT FK_measurement_unit FOREIGN KEY(unit_id) REFERENCES test.unit_result(unit_id),
    CONSTRAINT FK_measurement_item FOREIGN KEY(test_item_id) REFERENCES mdm.test_item_definition(test_item_id),
    CONSTRAINT CK_measurement_status CHECK(measurement_status IN('MEASURED','OVER_RANGE','UNDER_RANGE','NOT_TESTED','MISSING','INVALID','NOT_APPLICABLE'))
);
GO
CREATE NONCLUSTERED INDEX IX_measurement_unit ON test.measurement(unit_id,test_item_id) INCLUDE(value_numeric,value_text,measurement_status,tester_pass_flag);
GO

CREATE TABLE test.measurement_evaluation (
    evaluation_id bigint IDENTITY(1,1) NOT NULL,
    measurement_id bigint NOT NULL,
    evaluation_type varchar(32) NOT NULL,
    evaluation_scope_key nvarchar(200) NOT NULL,
    spec_binding_id bigint NULL,
    spec_item_id bigint NULL,
    lsl_applied float(53) NULL,
    usl_applied float(53) NULL,
    lower_operator_applied nvarchar(8) NULL,
    upper_operator_applied nvarchar(8) NULL,
    evaluation_result varchar(32) NOT NULL,
    evaluation_reason nvarchar(500) NULL,
    processing_run_id bigint NULL,
    is_current bit NOT NULL CONSTRAINT DF_measurement_eval_current DEFAULT(1),
    evaluated_at_utc datetime2(3) NOT NULL CONSTRAINT DF_measurement_eval_time DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_measurement_evaluation PRIMARY KEY CLUSTERED(evaluation_id),
    CONSTRAINT FK_measurement_eval_measurement FOREIGN KEY(measurement_id) REFERENCES test.measurement(measurement_id),
    CONSTRAINT FK_measurement_eval_binding FOREIGN KEY(spec_binding_id) REFERENCES mdm.spec_binding(spec_binding_id),
    CONSTRAINT FK_measurement_eval_spec_item FOREIGN KEY(spec_item_id) REFERENCES mdm.spec_item(spec_item_id),
    CONSTRAINT FK_measurement_eval_processing FOREIGN KEY(processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT CK_measurement_eval_type CHECK(evaluation_type IN('SPEC','PAT','SBL','SAFE_LAUNCH','OTHER')),
    CONSTRAINT CK_measurement_eval_result CHECK(evaluation_result IN('PASS','FAIL','NOT_EVALUATED','NO_MATCH','CONFIG_AMBIGUOUS','INVALID_VALUE'))
);
GO
CREATE UNIQUE NONCLUSTERED INDEX UX_measurement_eval_current
ON test.measurement_evaluation(measurement_id,evaluation_type,evaluation_scope_key) WHERE is_current=1;
GO

CREATE TABLE test.unit_bin_evaluation (
    unit_bin_evaluation_id bigint IDENTITY(1,1) NOT NULL,
    unit_id bigint NOT NULL,
    bin_type varchar(16) NOT NULL,
    raw_bin_code nvarchar(32) NOT NULL,
    bin_mapping_set_id bigint NULL,
    bin_definition_id bigint NULL,
    mapping_status varchar(32) NOT NULL,
    is_pass_snapshot bit NULL,
    failure_mode_snapshot nvarchar(300) NULL,
    processing_run_id bigint NULL,
    evaluated_at_utc datetime2(3) NOT NULL CONSTRAINT DF_unit_bin_eval_time DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_unit_bin_evaluation PRIMARY KEY CLUSTERED(unit_bin_evaluation_id),
    CONSTRAINT FK_unit_bin_eval_unit FOREIGN KEY(unit_id) REFERENCES test.unit_result(unit_id),
    CONSTRAINT FK_unit_bin_eval_set FOREIGN KEY(bin_mapping_set_id) REFERENCES mdm.bin_mapping_set(bin_mapping_set_id),
    CONSTRAINT FK_unit_bin_eval_definition FOREIGN KEY(bin_definition_id) REFERENCES mdm.bin_definition(bin_definition_id),
    CONSTRAINT FK_unit_bin_eval_processing FOREIGN KEY(processing_run_id) REFERENCES ingestion.processing_run(processing_run_id),
    CONSTRAINT CK_unit_bin_eval_type CHECK(bin_type IN('CP_BIN','SOFT_BIN','HARD_BIN','OTHER')),
    CONSTRAINT CK_unit_bin_mapping_status CHECK(mapping_status IN('MATCHED','NO_MATCH','CONFIG_AMBIGUOUS','INVALID'))
);
GO

CREATE TABLE trace.unit_traceability (
    trace_id bigint IDENTITY(1,1) NOT NULL,
    source_unit_id bigint NOT NULL,
    target_unit_id bigint NOT NULL,
    trace_type varchar(32) NOT NULL,
    confidence decimal(6,5) NULL,
    source_system nvarchar(64) NULL,
    source_ref nvarchar(500) NULL,
    metadata_json nvarchar(max) NULL,
    created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_trace_created DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_unit_traceability PRIMARY KEY CLUSTERED(trace_id),
    CONSTRAINT FK_trace_source FOREIGN KEY(source_unit_id) REFERENCES test.unit_result(unit_id),
    CONSTRAINT FK_trace_target FOREIGN KEY(target_unit_id) REFERENCES test.unit_result(unit_id),
    CONSTRAINT CK_trace_not_self CHECK(source_unit_id<>target_unit_id),
    CONSTRAINT CK_trace_confidence CHECK(confidence IS NULL OR(confidence>=0 AND confidence<=1))
);
GO

CREATE TABLE governance.audit_log (
    audit_id bigint IDENTITY(1,1) NOT NULL,
    actor nvarchar(128) NULL,
    operation varchar(64) NOT NULL,
    entity_type varchar(128) NOT NULL,
    entity_id nvarchar(128) NULL,
    before_json nvarchar(max) NULL,
    after_json nvarchar(max) NULL,
    reason nvarchar(1000) NULL,
    correlation_id nvarchar(128) NULL,
    occurred_at_utc datetime2(3) NOT NULL CONSTRAINT DF_audit_time DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_audit_log PRIMARY KEY CLUSTERED(audit_id)
);
GO
