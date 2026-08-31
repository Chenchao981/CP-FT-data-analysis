SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'test.measurement', N'U') IS NULL
    RAISERROR('sql2014_0023 blocked: test.measurement is missing.',16,1);
IF OBJECT_ID(N'test.unit_result', N'U') IS NULL
    RAISERROR('sql2014_0023 blocked: test.unit_result is missing.',16,1);
IF OBJECT_ID(N'test.unit_bin_evaluation', N'U') IS NULL
    RAISERROR('sql2014_0023 blocked: test.unit_bin_evaluation is missing.',16,1);
GO

/*
  Parameter-first access for descriptive, relationship, spatial, and quality
  analysis. measurement_id is the clustered key and is therefore carried by
  every nonclustered leaf row without being repeated as an INCLUDE column.
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.objects AS o ON o.object_id=i.object_id
    JOIN sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'test'
      AND o.name=N'measurement'
      AND i.name=N'IX_measurement_analytics_item_unit'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_measurement_analytics_item_unit
    ON test.measurement(test_item_id,unit_id)
    INCLUDE(value_numeric,measurement_status);
END;
GO

/*
  Dataset/run and optional wafer pruning while covering the unit attributes
  rendered by overview, detail, relationship, and wafer analysis.
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.objects AS o ON o.object_id=i.object_id
    JOIN sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'test'
      AND o.name=N'unit_result'
      AND i.name=N'IX_unit_result_analytics_run_wafer_unit'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_unit_result_analytics_run_wafer_unit
    ON test.unit_result(run_id,wafer_id,unit_id)
    INCLUDE(unit_sequence,x_coord,y_coord,overall_result,soft_bin,hard_bin);
END;
GO

/*
  Snapshot-only bin consumers filter by unit, status, raw code, and bin type.
  The INCLUDE list prevents analytics from re-reading mutable master mappings.
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.objects AS o ON o.object_id=i.object_id
    JOIN sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'test'
      AND o.name=N'unit_bin_evaluation'
      AND i.name=N'IX_unit_bin_evaluation_analytics_snapshot'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_unit_bin_evaluation_analytics_snapshot
    ON test.unit_bin_evaluation(unit_id,mapping_status,raw_bin_code,bin_type)
    INCLUDE(bin_mapping_set_id,bin_definition_id,is_pass_snapshot,
            failure_mode_snapshot,processing_run_id,evaluated_at_utc);
END;
GO

/* Alembic verifies its version-row UPDATE through pyodbc rowcount. */
SET NOCOUNT OFF;
GO
