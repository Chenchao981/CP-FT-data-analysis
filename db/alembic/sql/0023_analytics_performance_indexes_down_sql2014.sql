SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.objects AS o ON o.object_id=i.object_id
    JOIN sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'test'
      AND o.name=N'unit_bin_evaluation'
      AND i.name=N'IX_unit_bin_evaluation_analytics_snapshot'
)
    DROP INDEX IX_unit_bin_evaluation_analytics_snapshot
    ON test.unit_bin_evaluation;
GO

IF EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.objects AS o ON o.object_id=i.object_id
    JOIN sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'test'
      AND o.name=N'unit_result'
      AND i.name=N'IX_unit_result_analytics_run_wafer_unit'
)
    DROP INDEX IX_unit_result_analytics_run_wafer_unit
    ON test.unit_result;
GO

IF EXISTS (
    SELECT 1
    FROM sys.indexes AS i
    JOIN sys.objects AS o ON o.object_id=i.object_id
    JOIN sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'test'
      AND o.name=N'measurement'
      AND i.name=N'IX_measurement_analytics_item_unit'
)
    DROP INDEX IX_measurement_analytics_item_unit
    ON test.measurement;
GO

SET NOCOUNT OFF;
GO
