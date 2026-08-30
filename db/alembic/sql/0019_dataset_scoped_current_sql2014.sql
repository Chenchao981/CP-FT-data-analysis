SET XACT_ABORT ON;
GO

/*
Processing Run Current is derived from the Current Dataset Versions that link it.
The former filtered unique index on source_file_id incorrectly made an immutable
Source a singleton analysis.  One Source may now back independent Current
Datasets, while UX_dataset_version_current remains the Dataset-level invariant.
*/
IF OBJECT_ID(N'ingestion.processing_run', N'U') IS NULL
    RAISERROR('sql2014_0019 blocked: ingestion.processing_run is missing.', 16, 1);
IF OBJECT_ID(N'dataset.dataset_version', N'U') IS NULL
    RAISERROR('sql2014_0019 blocked: dataset.dataset_version is missing.', 16, 1);
IF OBJECT_ID(N'dataset.dataset_version_run', N'U') IS NULL
    RAISERROR('sql2014_0019 blocked: dataset.dataset_version_run is missing.', 16, 1);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'dataset.dataset_version')
      AND name=N'UX_dataset_version_current'
      AND is_unique=1
      AND has_filter=1
)
    RAISERROR('sql2014_0019 blocked: Dataset Current unique index is missing.', 16, 1);
GO

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.processing_run')
      AND name=N'UX_processing_run_current'
)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE object_id=OBJECT_ID(N'ingestion.processing_run')
          AND name=N'UX_processing_run_current'
          AND is_unique=1
          AND has_filter=1
          AND filter_definition LIKE N'%is_current%'
          AND filter_definition LIKE N'%PUBLISHED%'
          AND 1=(
              SELECT COUNT(*) FROM sys.index_columns ic
              WHERE ic.object_id=OBJECT_ID(N'ingestion.processing_run')
                AND ic.index_id=(
                    SELECT index_id FROM sys.indexes
                    WHERE object_id=OBJECT_ID(N'ingestion.processing_run')
                      AND name=N'UX_processing_run_current'
                )
                AND ic.key_ordinal>0
          )
          AND 1=(
              SELECT COUNT(*) FROM sys.index_columns ic
              JOIN sys.columns c
                ON c.object_id=ic.object_id AND c.column_id=ic.column_id
              WHERE ic.object_id=OBJECT_ID(N'ingestion.processing_run')
                AND ic.index_id=(
                    SELECT index_id FROM sys.indexes
                    WHERE object_id=OBJECT_ID(N'ingestion.processing_run')
                      AND name=N'UX_processing_run_current'
                )
                AND ic.key_ordinal>0 AND c.name=N'source_file_id'
          )
    )
    BEGIN
        RAISERROR('sql2014_0019 blocked: UX_processing_run_current has an unexpected definition.', 16, 1);
        RETURN;
    END;

    DROP INDEX UX_processing_run_current ON ingestion.processing_run;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.processing_run')
      AND name=N'IX_processing_run_source_state'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_processing_run_source_state
    ON ingestion.processing_run(source_file_id,status,is_current,processing_run_id)
    INCLUDE(supersedes_processing_run_id,finished_at_utc);
END;
GO

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.processing_run')
      AND name=N'IX_processing_run_source_state'
      AND is_unique=1
)
    RAISERROR('sql2014_0019 blocked: replacement Processing Run index must be non-unique.', 16, 1);
GO
