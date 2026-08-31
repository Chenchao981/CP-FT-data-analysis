from __future__ import annotations

"""Versioned Bin Mapping resolution and immutable snapshot materialization.

The resolver deliberately reads only approved MDM rows.  It never creates a Mapping
Set or Definition and never derives PASS/FAIL semantics from a raw Bin value.
"""

from sqlalchemy import Connection, text


class BinMappingMaterializationError(ValueError):
    pass


_MATERIALIZE_SQL = r"""
SET NOCOUNT ON;

DECLARE @bin_mapping_lock_result int;
DECLARE @evaluated_at_utc datetime2(3) = SYSUTCDATETIME();

EXEC @bin_mapping_lock_result = sys.sp_getapplock
    @Resource=:lock_resource,
    @LockMode='Exclusive',
    @LockOwner='Transaction',
    @LockTimeout=30000;

IF @bin_mapping_lock_result < 0
BEGIN
    RAISERROR('BIN_MAPPING_MATERIALIZATION_LOCK_FAILED', 16, 1);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1
    FROM ingestion.processing_run
    WHERE processing_run_id=:processing_run_id
)
BEGIN
    RAISERROR('BIN_MAPPING_PROCESSING_RUN_NOT_FOUND', 16, 1);
    RETURN;
END;

IF OBJECT_ID('tempdb..#tms_bin_mapping_stage') IS NOT NULL
    DROP TABLE #tms_bin_mapping_stage;

CREATE TABLE #tms_bin_mapping_stage (
    unit_id bigint NOT NULL,
    bin_type varchar(16) NOT NULL,
    raw_bin_code nvarchar(32) NOT NULL,
    bin_mapping_set_id bigint NULL,
    bin_definition_id bigint NULL,
    mapping_status varchar(32) NOT NULL,
    is_pass_snapshot bit NULL,
    failure_mode_snapshot nvarchar(300) NULL,
    processing_run_id bigint NOT NULL,
    evaluated_at_utc datetime2(3) NOT NULL,
    PRIMARY KEY CLUSTERED(unit_id, bin_type)
);

;WITH run_context AS (
    SELECT
        tr.run_id,
        tr.supplier_id,
        tr.product_id,
        tr.test_stage,
        tr.program_version_id,
        COALESCE(tr.started_at_utc, pr.started_at_utc) AS event_time_utc
    FROM test.test_run tr
    JOIN ingestion.processing_run pr
      ON pr.processing_run_id=tr.processing_run_id
    WHERE tr.processing_run_id=:processing_run_id
),
eligible_mapping_set AS (
    SELECT
        rc.run_id,
        bms.bin_mapping_set_id,
        sp.priority,
        DENSE_RANK() OVER (
            PARTITION BY rc.run_id
            ORDER BY sp.priority DESC
        ) AS priority_rank
    FROM run_context rc
    JOIN mdm.bin_mapping_set bms
      ON bms.active=1
     AND (bms.supplier_id IS NULL OR bms.supplier_id=rc.supplier_id)
     AND (bms.product_id IS NULL OR bms.product_id=rc.product_id)
     AND (bms.test_stage IS NULL OR bms.test_stage=rc.test_stage)
     AND (
            bms.program_version_id IS NULL
            OR bms.program_version_id=rc.program_version_id
         )
     AND (
            bms.effective_from_utc IS NULL
            OR bms.effective_from_utc<=rc.event_time_utc
         )
     AND (
            bms.effective_to_utc IS NULL
            OR bms.effective_to_utc>rc.event_time_utc
         )
    JOIN mdm.scope_priority sp
      ON sp.scope_code=bms.scope_code
     AND sp.active=1
),
resolved_mapping_set AS (
    SELECT
        run_id,
        COUNT_BIG(*) AS top_candidate_count,
        MIN(bin_mapping_set_id) AS bin_mapping_set_id
    FROM eligible_mapping_set
    WHERE priority_rank=1
    GROUP BY run_id
),
unit_bin AS (
    SELECT
        ur.unit_id,
        ur.run_id,
        CAST('CP_BIN' AS varchar(16)) AS bin_type,
        CAST(COALESCE(ur.soft_bin, ur.hard_bin, N'') AS nvarchar(32)) AS raw_bin_code
    FROM test.unit_result ur
    JOIN test.test_run tr ON tr.run_id=ur.run_id
    WHERE tr.processing_run_id=:processing_run_id
      AND tr.test_stage='CP'

    UNION ALL

    SELECT
        ur.unit_id,
        ur.run_id,
        CAST('SOFT_BIN' AS varchar(16)) AS bin_type,
        CAST(ur.soft_bin AS nvarchar(32)) AS raw_bin_code
    FROM test.unit_result ur
    JOIN test.test_run tr ON tr.run_id=ur.run_id
    WHERE tr.processing_run_id=:processing_run_id
      AND tr.test_stage='FT'
      AND ur.soft_bin IS NOT NULL

    UNION ALL

    SELECT
        ur.unit_id,
        ur.run_id,
        CAST('HARD_BIN' AS varchar(16)) AS bin_type,
        CAST(ur.hard_bin AS nvarchar(32)) AS raw_bin_code
    FROM test.unit_result ur
    JOIN test.test_run tr ON tr.run_id=ur.run_id
    WHERE tr.processing_run_id=:processing_run_id
      AND tr.test_stage='FT'
      AND ur.hard_bin IS NOT NULL
),
definition_resolution AS (
    SELECT
        ub.unit_id,
        ub.bin_type,
        ub.raw_bin_code,
        COALESCE(rms.top_candidate_count, 0) AS top_candidate_count,
        CASE
            WHEN rms.top_candidate_count=1 THEN rms.bin_mapping_set_id
            ELSE NULL
        END AS unique_mapping_set_id,
        COUNT_BIG(bd.bin_definition_id) AS definition_count,
        MIN(bd.bin_definition_id) AS bin_definition_id,
        MIN(CAST(bd.is_pass AS tinyint)) AS is_pass_value,
        MIN(bd.failure_mode) AS failure_mode
    FROM unit_bin ub
    LEFT JOIN resolved_mapping_set rms ON rms.run_id=ub.run_id
    LEFT JOIN mdm.bin_definition bd
      ON rms.top_candidate_count=1
     AND bd.bin_mapping_set_id=rms.bin_mapping_set_id
     AND bd.bin_type=ub.bin_type
     AND bd.bin_code=ub.raw_bin_code
    GROUP BY
        ub.unit_id,
        ub.bin_type,
        ub.raw_bin_code,
        rms.top_candidate_count,
        rms.bin_mapping_set_id
),
classified AS (
    SELECT
        dr.*,
        CASE
            WHEN LEN(LTRIM(RTRIM(dr.raw_bin_code)))=0 THEN 'INVALID'
            WHEN dr.top_candidate_count=0 THEN 'NO_MATCH'
            WHEN dr.top_candidate_count>1 THEN 'CONFIG_AMBIGUOUS'
            WHEN dr.definition_count=0 THEN 'NO_MATCH'
            WHEN dr.definition_count>1 THEN 'CONFIG_AMBIGUOUS'
            ELSE 'MATCHED'
        END AS mapping_status
    FROM definition_resolution dr
)
INSERT #tms_bin_mapping_stage(
    unit_id,
    bin_type,
    raw_bin_code,
    bin_mapping_set_id,
    bin_definition_id,
    mapping_status,
    is_pass_snapshot,
    failure_mode_snapshot,
    processing_run_id,
    evaluated_at_utc
)
SELECT
    unit_id,
    bin_type,
    raw_bin_code,
    CASE WHEN mapping_status='MATCHED' THEN unique_mapping_set_id END,
    CASE WHEN mapping_status='MATCHED' THEN bin_definition_id END,
    mapping_status,
    CASE WHEN mapping_status='MATCHED' THEN CAST(is_pass_value AS bit) END,
    CASE WHEN mapping_status='MATCHED' THEN failure_mode END,
    :processing_run_id,
    @evaluated_at_utc
FROM classified;

IF EXISTS (
    SELECT 1
    FROM test.unit_bin_evaluation ube WITH (UPDLOCK, HOLDLOCK)
    JOIN #tms_bin_mapping_stage stage
      ON stage.unit_id=ube.unit_id
     AND stage.bin_type=ube.bin_type
    GROUP BY ube.unit_id, ube.bin_type
    HAVING COUNT_BIG(*)>1
)
BEGIN
    RAISERROR('BIN_MAPPING_DUPLICATE_UNIT_TYPE', 16, 1);
    RETURN;
END;

UPDATE target WITH (UPDLOCK, HOLDLOCK)
SET
    target.raw_bin_code=stage.raw_bin_code,
    target.bin_mapping_set_id=stage.bin_mapping_set_id,
    target.bin_definition_id=stage.bin_definition_id,
    target.mapping_status=stage.mapping_status,
    target.is_pass_snapshot=stage.is_pass_snapshot,
    target.failure_mode_snapshot=stage.failure_mode_snapshot,
    target.processing_run_id=stage.processing_run_id,
    target.evaluated_at_utc=stage.evaluated_at_utc
FROM test.unit_bin_evaluation target
JOIN #tms_bin_mapping_stage stage
  ON stage.unit_id=target.unit_id
 AND stage.bin_type=target.bin_type;

INSERT test.unit_bin_evaluation(
    unit_id,
    bin_type,
    raw_bin_code,
    bin_mapping_set_id,
    bin_definition_id,
    mapping_status,
    is_pass_snapshot,
    failure_mode_snapshot,
    processing_run_id,
    evaluated_at_utc
)
SELECT
    stage.unit_id,
    stage.bin_type,
    stage.raw_bin_code,
    stage.bin_mapping_set_id,
    stage.bin_definition_id,
    stage.mapping_status,
    stage.is_pass_snapshot,
    stage.failure_mode_snapshot,
    stage.processing_run_id,
    stage.evaluated_at_utc
FROM #tms_bin_mapping_stage stage
WHERE NOT EXISTS (
    SELECT 1
    FROM test.unit_bin_evaluation existing WITH (UPDLOCK, HOLDLOCK)
    WHERE existing.unit_id=stage.unit_id
      AND existing.bin_type=stage.bin_type
);

DROP TABLE #tms_bin_mapping_stage;
"""


def materialize_processing_run_bin_mappings(
    connection: Connection,
    *,
    processing_run_id: int,
) -> None:
    """Resolve and snapshot every applicable physical Bin in one import transaction.

    CP has one logical ``CP_BIN`` (soft Bin first, then hard Bin).  FT preserves each
    physical soft/hard Bin that the source actually supplied.  A missing CP raw Bin is
    stored as an empty raw snapshot with ``INVALID``; a format with no FT Bin columns
    produces no fabricated evaluation rows.  Effective-time resolution uses the test
    run UTC timestamp when known and otherwise the processing-run UTC start time.
    """

    if isinstance(processing_run_id, bool) or processing_run_id <= 0:
        raise BinMappingMaterializationError("processing_run_id must be positive")
    connection.execute(
        text(_MATERIALIZE_SQL),
        {
            "processing_run_id": int(processing_run_id),
            "lock_resource": f"TMS_BIN_MAPPING_PROCESSING_RUN:{int(processing_run_id)}",
        },
    )
