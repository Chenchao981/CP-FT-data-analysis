SET XACT_ABORT ON;
GO

/*
Route A atomic finalize is opt-in. Existing Jobs keep the legacy protocol until
the Worker explicitly creates an ATOMIC_V1 Job after all participating
components have been deployed.
*/
IF OBJECT_ID(N'ingestion.processing_job', N'U') IS NULL
    RAISERROR('sql2014_0015 blocked: ingestion.processing_job is missing.', 16, 1);
IF OBJECT_ID(N'ingestion.processing_run', N'U') IS NULL
    RAISERROR('sql2014_0015 blocked: ingestion.processing_run is missing.', 16, 1);
IF OBJECT_ID(N'ingestion.import_batch', N'U') IS NULL
    RAISERROR('sql2014_0015 blocked: ingestion.import_batch is missing.', 16, 1);
IF OBJECT_ID(N'ingestion.import_batch_file', N'U') IS NULL
    RAISERROR('sql2014_0015 blocked: ingestion.import_batch_file is missing.', 16, 1);
IF OBJECT_ID(N'dataset.dataset_version', N'U') IS NULL
    RAISERROR('sql2014_0015 blocked: dataset.dataset_version is missing.', 16, 1);
GO

IF COL_LENGTH(N'ingestion.processing_job', N'finalize_protocol') IS NULL
BEGIN
    ALTER TABLE ingestion.processing_job ADD
        finalize_protocol varchar(16) NOT NULL
            CONSTRAINT DF_processing_job_finalize_protocol DEFAULT('LEGACY')
            WITH VALUES;
END;
ELSE IF EXISTS (
    SELECT 1
    FROM sys.columns AS c
    JOIN sys.types AS t ON t.user_type_id=c.user_type_id
    WHERE c.object_id=OBJECT_ID(N'ingestion.processing_job')
      AND c.name=N'finalize_protocol'
      AND (t.name<>N'varchar' OR c.max_length<>16 OR c.is_nullable<>0)
)
BEGIN
    RAISERROR(
        'sql2014_0015 blocked: processing_job.finalize_protocol has an incompatible definition.',
        16,
        1
    );
END;
GO

IF EXISTS (
    SELECT 1
    FROM ingestion.processing_job
    WHERE finalize_protocol NOT IN('LEGACY','ATOMIC_V1')
)
    RAISERROR('sql2014_0015 blocked: invalid finalize_protocol data exists.', 16, 1);
GO

IF EXISTS (
    SELECT 1 FROM ingestion.processing_job
    WHERE job_type='INITIAL_IMPORT' AND status IN('QUEUED','RUNNING')
)
    RAISERROR(
        'sql2014_0015 blocked: drain active INITIAL_IMPORT jobs before enabling ATOMIC_V1.',
        16,
        1
    );
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id=OBJECT_ID(N'ingestion.processing_job')
      AND name=N'DF_processing_job_finalize_protocol'
)
BEGIN
    IF EXISTS (
        SELECT 1
        FROM sys.columns
        WHERE object_id=OBJECT_ID(N'ingestion.processing_job')
          AND name=N'finalize_protocol'
          AND default_object_id<>0
    )
        RAISERROR(
            'sql2014_0015 blocked: finalize_protocol has an unexpected default constraint.',
            16,
            1
        );
    ELSE
        ALTER TABLE ingestion.processing_job ADD
            CONSTRAINT DF_processing_job_finalize_protocol
            DEFAULT('LEGACY') FOR finalize_protocol;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'ingestion.processing_job')
      AND name=N'CK_processing_job_finalize_protocol'
)
    ALTER TABLE ingestion.processing_job ADD
        CONSTRAINT CK_processing_job_finalize_protocol CHECK(
            (job_type='INITIAL_IMPORT' AND finalize_protocol IN('LEGACY','ATOMIC_V1'))
            OR (job_type<>'INITIAL_IMPORT' AND finalize_protocol='LEGACY')
        );
GO

/*
This table records only an explicit Writer decision. The migration deliberately
does not backfill historical Batch membership as if it were Writer-verified
lineage.
*/
IF OBJECT_ID(N'ingestion.processing_run_input_file', N'U') IS NULL
BEGIN
    CREATE TABLE ingestion.processing_run_input_file (
        processing_run_id bigint NOT NULL,
        import_batch_file_id bigint NOT NULL,
        lineage_basis varchar(32) NOT NULL,
        created_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_processing_run_input_file_created DEFAULT(SYSUTCDATETIME()),
        CONSTRAINT PK_processing_run_input_file PRIMARY KEY CLUSTERED(
            processing_run_id,
            import_batch_file_id
        ),
        CONSTRAINT FK_processing_run_input_file_run FOREIGN KEY(processing_run_id)
            REFERENCES ingestion.processing_run(processing_run_id),
        CONSTRAINT FK_processing_run_input_file_batch_file FOREIGN KEY(import_batch_file_id)
            REFERENCES ingestion.import_batch_file(import_batch_file_id),
        CONSTRAINT CK_processing_run_input_file_basis CHECK(
            lineage_basis IN('WRITER_VERIFIED','LEGACY_BATCH_MEMBERSHIP')
        )
    );
END;
ELSE IF (
    SELECT COUNT(*)
    FROM sys.columns
    WHERE object_id=OBJECT_ID(N'ingestion.processing_run_input_file')
)<>4 OR (
    SELECT COUNT(*)
    FROM sys.columns
    WHERE object_id=OBJECT_ID(N'ingestion.processing_run_input_file')
      AND name IN(
          N'processing_run_id',
          N'import_batch_file_id',
          N'lineage_basis',
          N'created_at_utc'
      )
)<>4
BEGIN
    RAISERROR(
        'sql2014_0015 blocked: processing_run_input_file has an incompatible column contract.',
        16,
        1
    );
END;
GO

IF OBJECT_ID(N'ingestion.processing_run_input_file', N'U') IS NOT NULL
   AND (
       SELECT COUNT(*)
       FROM sys.objects
       WHERE parent_object_id=OBJECT_ID(N'ingestion.processing_run_input_file')
         AND name IN(
             N'PK_processing_run_input_file',
             N'FK_processing_run_input_file_run',
             N'FK_processing_run_input_file_batch_file',
             N'CK_processing_run_input_file_basis'
         )
   )<>4
BEGIN
    RAISERROR(
        'sql2014_0015 blocked: processing_run_input_file constraints are incomplete.',
        16,
        1
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.processing_run_input_file')
      AND name=N'IX_processing_run_input_file_batch_file'
)
    CREATE NONCLUSTERED INDEX IX_processing_run_input_file_batch_file
    ON ingestion.processing_run_input_file(import_batch_file_id,processing_run_id);
GO

/*
The intent is durable recovery evidence. Live lease ownership remains on
ingestion.processing_job; finalized_lease_token proves which lease committed
the terminal state.
*/
IF OBJECT_ID(N'ingestion.initial_import_finalize_intent', N'U') IS NULL
BEGIN
    CREATE TABLE ingestion.initial_import_finalize_intent (
        job_id bigint NOT NULL,
        import_batch_id bigint NOT NULL,
        processing_run_id bigint NOT NULL,
        dataset_version_id bigint NOT NULL,
        input_manifest_sha256 char(64) NOT NULL,
        input_manifest_json nvarchar(max) NOT NULL,
        status varchar(16) NOT NULL
            CONSTRAINT DF_initial_import_finalize_status DEFAULT('STAGED'),
        staged_attempt_count int NOT NULL,
        staged_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_initial_import_finalize_staged DEFAULT(SYSUTCDATETIME()),
        finalized_at_utc datetime2(3) NULL,
        finalized_lease_token uniqueidentifier NULL,
        aborted_at_utc datetime2(3) NULL,
        abort_error_code nvarchar(64) NULL,
        abort_error_message nvarchar(2000) NULL,
        row_version rowversion NOT NULL,
        CONSTRAINT PK_initial_import_finalize_intent PRIMARY KEY CLUSTERED(job_id),
        CONSTRAINT FK_initial_import_finalize_job FOREIGN KEY(job_id)
            REFERENCES ingestion.processing_job(job_id),
        CONSTRAINT FK_initial_import_finalize_batch FOREIGN KEY(import_batch_id)
            REFERENCES ingestion.import_batch(import_batch_id),
        CONSTRAINT FK_initial_import_finalize_run FOREIGN KEY(processing_run_id)
            REFERENCES ingestion.processing_run(processing_run_id),
        CONSTRAINT FK_initial_import_finalize_version FOREIGN KEY(dataset_version_id)
            REFERENCES dataset.dataset_version(dataset_version_id),
        CONSTRAINT UQ_initial_import_finalize_run UNIQUE(processing_run_id),
        CONSTRAINT UQ_initial_import_finalize_version UNIQUE(dataset_version_id),
        CONSTRAINT CK_initial_import_finalize_manifest CHECK(
            LEN(input_manifest_sha256)=64
            AND input_manifest_sha256 NOT LIKE '%[^0-9A-Fa-f]%'
            AND DATALENGTH(input_manifest_json)>0
        ),
        CONSTRAINT CK_initial_import_finalize_attempt CHECK(
            staged_attempt_count BETWEEN 1 AND 20
        ),
        CONSTRAINT CK_initial_import_finalize_status CHECK(
            status IN('STAGED','FINALIZED','ABORTED')
        ),
        CONSTRAINT CK_initial_import_finalize_terminal CHECK(
            (
                status='STAGED'
                AND finalized_at_utc IS NULL
                AND finalized_lease_token IS NULL
                AND aborted_at_utc IS NULL
                AND abort_error_code IS NULL
                AND abort_error_message IS NULL
            )
            OR
            (
                status='FINALIZED'
                AND finalized_at_utc IS NOT NULL
                AND finalized_lease_token IS NOT NULL
                AND aborted_at_utc IS NULL
                AND abort_error_code IS NULL
                AND abort_error_message IS NULL
            )
            OR
            (
                status='ABORTED'
                AND finalized_at_utc IS NULL
                AND finalized_lease_token IS NULL
                AND aborted_at_utc IS NOT NULL
                AND abort_error_code IS NOT NULL
                AND abort_error_message IS NOT NULL
            )
        )
    );
END;
ELSE IF (
    SELECT COUNT(*)
    FROM sys.columns
    WHERE object_id=OBJECT_ID(N'ingestion.initial_import_finalize_intent')
)<>15 OR (
    SELECT COUNT(*)
    FROM sys.columns
    WHERE object_id=OBJECT_ID(N'ingestion.initial_import_finalize_intent')
      AND name IN(
          N'job_id',
          N'import_batch_id',
          N'processing_run_id',
          N'dataset_version_id',
          N'input_manifest_sha256',
          N'input_manifest_json',
          N'status',
          N'staged_attempt_count',
          N'staged_at_utc',
          N'finalized_at_utc',
          N'finalized_lease_token',
          N'aborted_at_utc',
          N'abort_error_code',
          N'abort_error_message',
          N'row_version'
      )
)<>15
BEGIN
    RAISERROR(
        'sql2014_0015 blocked: initial_import_finalize_intent has an incompatible column contract.',
        16,
        1
    );
END;
GO

IF OBJECT_ID(N'ingestion.initial_import_finalize_intent', N'U') IS NOT NULL
   AND (
       SELECT COUNT(*)
       FROM sys.objects
       WHERE parent_object_id=OBJECT_ID(N'ingestion.initial_import_finalize_intent')
         AND name IN(
             N'PK_initial_import_finalize_intent',
             N'FK_initial_import_finalize_job',
             N'FK_initial_import_finalize_batch',
             N'FK_initial_import_finalize_run',
             N'FK_initial_import_finalize_version',
             N'UQ_initial_import_finalize_run',
             N'UQ_initial_import_finalize_version',
             N'CK_initial_import_finalize_manifest',
             N'CK_initial_import_finalize_attempt',
             N'CK_initial_import_finalize_status',
             N'CK_initial_import_finalize_terminal'
         )
   )<>11
BEGIN
    RAISERROR(
        'sql2014_0015 blocked: initial_import_finalize_intent constraints are incomplete.',
        16,
        1
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.initial_import_finalize_intent')
      AND name=N'IX_initial_import_finalize_recovery'
)
    CREATE NONCLUSTERED INDEX IX_initial_import_finalize_recovery
    ON ingestion.initial_import_finalize_intent(status,staged_at_utc,job_id)
    INCLUDE(
        import_batch_id,
        processing_run_id,
        dataset_version_id,
        staged_attempt_count
    );
GO
