from __future__ import annotations

"""Materialize immutable formal-Spec decisions for one canonical import.

The SQL deliberately reads only Released Spec master data.  Program limits in
``mdm.test_item_definition`` are identity/context evidence only and are never
used as acceptance limits.
"""

from collections.abc import Mapping

from sqlalchemy import Connection, text


class SpecEvaluationMaterializationError(ValueError):
    pass


_MATERIALIZE_SQL = r"""
SET NOCOUNT ON;

DECLARE @spec_evaluation_lock_result int;
DECLARE @evaluated_at_utc datetime2(3) = SYSUTCDATETIME();
DECLARE @has_explicit_spec_map bit = 0;

EXEC @spec_evaluation_lock_result = sys.sp_getapplock
    @Resource=:lock_resource,
    @LockMode='Exclusive',
    @LockOwner='Transaction',
    @LockTimeout=30000;

IF @spec_evaluation_lock_result < 0
BEGIN
    RAISERROR('SPEC_EVALUATION_MATERIALIZATION_LOCK_FAILED', 16, 1);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1
    FROM ingestion.processing_run
    WHERE processing_run_id=:processing_run_id
)
BEGIN
    RAISERROR('SPEC_EVALUATION_PROCESSING_RUN_NOT_FOUND', 16, 1);
    RETURN;
END;

IF OBJECT_ID('tempdb..#tms_explicit_run_spec') IS NOT NULL
    DROP TABLE #tms_explicit_run_spec;

CREATE TABLE #tms_explicit_run_spec (
    run_id bigint NOT NULL PRIMARY KEY CLUSTERED,
    spec_set_id bigint NOT NULL
);

{explicit_spec_insert}

IF EXISTS (SELECT 1 FROM #tms_explicit_run_spec)
    SET @has_explicit_spec_map = 1;

IF EXISTS (
    SELECT 1
    FROM #tms_explicit_run_spec explicit_map
    LEFT JOIN test.test_run tr
      ON tr.run_id=explicit_map.run_id
     AND tr.processing_run_id=:processing_run_id
    WHERE tr.run_id IS NULL
)
BEGIN
    RAISERROR('SPEC_EVALUATION_EXPLICIT_RUN_MISMATCH', 16, 1);
    RETURN;
END;

IF @has_explicit_spec_map=1 AND EXISTS (
    SELECT 1
    FROM test.test_run tr
    LEFT JOIN #tms_explicit_run_spec explicit_map
      ON explicit_map.run_id=tr.run_id
    WHERE tr.processing_run_id=:processing_run_id
      AND explicit_map.run_id IS NULL
)
BEGIN
    RAISERROR('SPEC_EVALUATION_EXPLICIT_MAP_INCOMPLETE', 16, 1);
    RETURN;
END;

IF OBJECT_ID('tempdb..#tms_spec_evaluation_stage') IS NOT NULL
    DROP TABLE #tms_spec_evaluation_stage;

CREATE TABLE #tms_spec_evaluation_stage (
    measurement_id bigint NOT NULL PRIMARY KEY CLUSTERED,
    spec_binding_id bigint NULL,
    spec_item_id bigint NULL,
    lsl_applied float(53) NULL,
    usl_applied float(53) NULL,
    lower_operator_applied nvarchar(8) NULL,
    upper_operator_applied nvarchar(8) NULL,
    evaluation_result varchar(32) NOT NULL,
    evaluation_reason nvarchar(500) NOT NULL,
    processing_run_id bigint NOT NULL,
    evaluated_at_utc datetime2(3) NOT NULL
);

;WITH run_context AS (
    SELECT
        tr.run_id,
        tr.supplier_id,
        tr.product_id,
        tr.test_stage,
        tr.program_version_id,
        COALESCE(tr.started_at_utc, pr.started_at_utc) AS event_time_utc,
        explicit_map.spec_set_id AS explicit_spec_set_id
    FROM test.test_run tr
    JOIN ingestion.processing_run pr
      ON pr.processing_run_id=tr.processing_run_id
    LEFT JOIN #tms_explicit_run_spec explicit_map
      ON explicit_map.run_id=tr.run_id
    WHERE tr.processing_run_id=:processing_run_id
),
eligible_generic_binding AS (
    SELECT
        rc.run_id,
        sb.spec_binding_id,
        sb.spec_set_id,
        priority.priority,
        DENSE_RANK() OVER (
            PARTITION BY rc.run_id
            ORDER BY priority.priority DESC
        ) AS priority_rank
    FROM run_context rc
    JOIN mdm.spec_binding sb
      ON rc.explicit_spec_set_id IS NULL
     AND sb.active=1
     AND (sb.supplier_id IS NULL OR sb.supplier_id=rc.supplier_id)
     AND (sb.product_id IS NULL OR sb.product_id=rc.product_id)
     AND (sb.test_stage IS NULL OR sb.test_stage=rc.test_stage)
     AND (
            sb.program_version_id IS NULL
            OR sb.program_version_id=rc.program_version_id
         )
     AND (
            sb.effective_from_utc IS NULL
            OR sb.effective_from_utc<=rc.event_time_utc
         )
     AND (
            sb.effective_to_utc IS NULL
            OR sb.effective_to_utc>rc.event_time_utc
         )
    JOIN mdm.scope_priority priority
      ON priority.scope_code=sb.scope_code
     AND priority.active=1
    JOIN mdm.spec_set spec_set
      ON spec_set.spec_set_id=sb.spec_set_id
     AND spec_set.status='RELEASED'
     AND (spec_set.product_id IS NULL OR spec_set.product_id=rc.product_id)
     AND (spec_set.test_stage IS NULL OR spec_set.test_stage=rc.test_stage)
     AND (
            spec_set.effective_from_utc IS NULL
            OR spec_set.effective_from_utc<=rc.event_time_utc
         )
     AND (
            spec_set.effective_to_utc IS NULL
            OR spec_set.effective_to_utc>rc.event_time_utc
         )
),
generic_resolution AS (
    SELECT
        run_id,
        COUNT_BIG(*) AS top_candidate_count,
        MIN(spec_binding_id) AS spec_binding_id,
        MIN(spec_set_id) AS spec_set_id
    FROM eligible_generic_binding
    WHERE priority_rank=1
    GROUP BY run_id
),
eligible_explicit_spec AS (
    SELECT rc.run_id, spec_set.spec_set_id
    FROM run_context rc
    JOIN mdm.spec_set spec_set
      ON spec_set.spec_set_id=rc.explicit_spec_set_id
     AND spec_set.status='RELEASED'
     AND (spec_set.product_id IS NULL OR spec_set.product_id=rc.product_id)
     AND (spec_set.test_stage IS NULL OR spec_set.test_stage=rc.test_stage)
     AND (
            spec_set.effective_from_utc IS NULL
            OR spec_set.effective_from_utc<=rc.event_time_utc
         )
     AND (
            spec_set.effective_to_utc IS NULL
            OR spec_set.effective_to_utc>rc.event_time_utc
         )
    WHERE rc.explicit_spec_set_id IS NOT NULL
),
eligible_explicit_binding AS (
    SELECT
        rc.run_id,
        sb.spec_binding_id,
        priority.priority,
        DENSE_RANK() OVER (
            PARTITION BY rc.run_id
            ORDER BY priority.priority DESC
        ) AS priority_rank
    FROM run_context rc
    JOIN eligible_explicit_spec explicit_spec
      ON explicit_spec.run_id=rc.run_id
    JOIN mdm.spec_binding sb
      ON sb.spec_set_id=explicit_spec.spec_set_id
     AND sb.active=1
     AND (sb.supplier_id IS NULL OR sb.supplier_id=rc.supplier_id)
     AND (sb.product_id IS NULL OR sb.product_id=rc.product_id)
     AND (sb.test_stage IS NULL OR sb.test_stage=rc.test_stage)
     AND (
            sb.program_version_id IS NULL
            OR sb.program_version_id=rc.program_version_id
         )
     AND (
            sb.effective_from_utc IS NULL
            OR sb.effective_from_utc<=rc.event_time_utc
         )
     AND (
            sb.effective_to_utc IS NULL
            OR sb.effective_to_utc>rc.event_time_utc
         )
    JOIN mdm.scope_priority priority
      ON priority.scope_code=sb.scope_code
     AND priority.active=1
),
explicit_binding_resolution AS (
    SELECT
        run_id,
        COUNT_BIG(*) AS top_candidate_count,
        MIN(spec_binding_id) AS spec_binding_id
    FROM eligible_explicit_binding
    WHERE priority_rank=1
    GROUP BY run_id
),
resolved_spec AS (
    SELECT
        rc.run_id,
        CASE
            WHEN rc.explicit_spec_set_id IS NOT NULL THEN explicit_spec.spec_set_id
            WHEN generic.top_candidate_count=1 THEN generic.spec_set_id
            ELSE NULL
        END AS spec_set_id,
        CASE
            WHEN rc.explicit_spec_set_id IS NOT NULL
                 AND explicit_binding.top_candidate_count=1
                THEN explicit_binding.spec_binding_id
            WHEN rc.explicit_spec_set_id IS NULL
                 AND generic.top_candidate_count=1
                THEN generic.spec_binding_id
            ELSE NULL
        END AS spec_binding_id,
        CASE
            WHEN rc.explicit_spec_set_id IS NOT NULL
                 AND explicit_spec.spec_set_id IS NULL
                THEN 'NO_MATCH'
            WHEN rc.explicit_spec_set_id IS NOT NULL THEN 'RESOLVED'
            WHEN COALESCE(generic.top_candidate_count, 0)=0 THEN 'NO_MATCH'
            WHEN generic.top_candidate_count>1 THEN 'CONFIG_AMBIGUOUS'
            ELSE 'RESOLVED'
        END AS spec_resolution
    FROM run_context rc
    LEFT JOIN eligible_explicit_spec explicit_spec
      ON explicit_spec.run_id=rc.run_id
    LEFT JOIN explicit_binding_resolution explicit_binding
      ON explicit_binding.run_id=rc.run_id
    LEFT JOIN generic_resolution generic
      ON generic.run_id=rc.run_id
),
item_resolution AS (
    SELECT
        measurement.measurement_id,
        measurement.value_numeric,
        measurement.measurement_status,
        resolved.spec_binding_id,
        resolved.spec_resolution,
        COUNT_BIG(spec_item.spec_item_id) AS spec_item_count,
        MIN(spec_item.spec_item_id) AS spec_item_id,
        MIN(spec_item.lsl) AS lsl,
        MIN(spec_item.usl) AS usl,
        MIN(spec_item.lower_operator) AS lower_operator,
        MIN(spec_item.upper_operator) AS upper_operator
    FROM test.measurement measurement
    JOIN test.unit_result unit_result
      ON unit_result.unit_id=measurement.unit_id
    JOIN test.test_run test_run
      ON test_run.run_id=unit_result.run_id
     AND test_run.processing_run_id=:processing_run_id
    JOIN mdm.test_item_definition test_item
      ON test_item.test_item_id=measurement.test_item_id
    JOIN resolved_spec resolved
      ON resolved.run_id=test_run.run_id
    LEFT JOIN mdm.spec_item spec_item
      ON spec_item.spec_set_id=resolved.spec_set_id
     AND spec_item.test_item_id=test_item.test_item_id
     AND (
            (spec_item.unit_code IS NULL AND test_item.unit_code IS NULL)
            OR spec_item.unit_code COLLATE Latin1_General_100_BIN2
               =test_item.unit_code COLLATE Latin1_General_100_BIN2
         )
     AND (
            (spec_item.condition_json IS NULL AND test_item.condition_json IS NULL)
            OR spec_item.condition_json COLLATE Latin1_General_100_BIN2
               =test_item.condition_json COLLATE Latin1_General_100_BIN2
         )
    GROUP BY
        measurement.measurement_id,
        measurement.value_numeric,
        measurement.measurement_status,
        resolved.spec_binding_id,
        resolved.spec_resolution
),
classified AS (
    SELECT
        item.*,
        CASE
            WHEN item.measurement_status IN (
                'MISSING','NOT_TESTED','NOT_APPLICABLE'
            ) THEN 'NOT_EVALUATED'
            WHEN item.measurement_status IN (
                'OVER_RANGE','UNDER_RANGE','INVALID'
            ) OR item.value_numeric IS NULL THEN 'INVALID_VALUE'
            WHEN item.spec_resolution='CONFIG_AMBIGUOUS'
                THEN 'CONFIG_AMBIGUOUS'
            WHEN item.spec_resolution='NO_MATCH' OR item.spec_item_count=0
                THEN 'NO_MATCH'
            WHEN item.spec_item_count>1 THEN 'CONFIG_AMBIGUOUS'
            WHEN (item.lsl IS NULL AND item.usl IS NULL)
              OR (item.lsl IS NOT NULL AND (
                    item.lower_operator IS NULL
                    OR item.lower_operator NOT IN (N'>=', N'>')
                 ))
              OR (item.usl IS NOT NULL AND (
                    item.upper_operator IS NULL
                    OR item.upper_operator NOT IN (N'<=', N'<')
                 ))
              OR (item.lsl IS NOT NULL AND item.usl IS NOT NULL AND (
                    item.lsl>item.usl
                    OR (
                        item.lsl=item.usl
                        AND (
                            item.lower_operator=N'>'
                            OR item.upper_operator=N'<'
                        )
                    )
                 ))
                THEN 'CONFIG_AMBIGUOUS'
            WHEN (item.lsl IS NOT NULL AND (
                    (item.lower_operator=N'>=' AND item.value_numeric<item.lsl)
                    OR (item.lower_operator=N'>' AND item.value_numeric<=item.lsl)
                 ))
              OR (item.usl IS NOT NULL AND (
                    (item.upper_operator=N'<=' AND item.value_numeric>item.usl)
                    OR (item.upper_operator=N'<' AND item.value_numeric>=item.usl)
                 ))
                THEN 'FAIL'
            ELSE 'PASS'
        END AS evaluation_result
    FROM item_resolution item
),
reasoned AS (
    SELECT
        classified.*,
        CASE classified.evaluation_result
            WHEN 'NOT_EVALUATED' THEN N'MEASUREMENT_STATUS_NOT_EVALUATED'
            WHEN 'INVALID_VALUE' THEN N'MEASUREMENT_VALUE_INVALID'
            WHEN 'NO_MATCH' THEN CASE
                WHEN classified.spec_resolution='NO_MATCH'
                    THEN N'FORMAL_SPEC_NOT_FOUND'
                ELSE N'FORMAL_SPEC_ITEM_NOT_FOUND'
            END
            WHEN 'CONFIG_AMBIGUOUS' THEN CASE
                WHEN classified.spec_resolution='CONFIG_AMBIGUOUS'
                    THEN N'FORMAL_SPEC_BINDING_AMBIGUOUS'
                WHEN classified.spec_item_count>1
                    THEN N'FORMAL_SPEC_ITEM_AMBIGUOUS'
                ELSE N'FORMAL_SPEC_LIMIT_CONFIG_INVALID'
            END
            WHEN 'FAIL' THEN N'FORMAL_SPEC_LIMIT_FAIL'
            ELSE N'FORMAL_SPEC_LIMIT_PASS'
        END AS evaluation_reason
    FROM classified
)
INSERT #tms_spec_evaluation_stage(
    measurement_id,
    spec_binding_id,
    spec_item_id,
    lsl_applied,
    usl_applied,
    lower_operator_applied,
    upper_operator_applied,
    evaluation_result,
    evaluation_reason,
    processing_run_id,
    evaluated_at_utc
)
SELECT
    measurement_id,
    spec_binding_id,
    CASE WHEN spec_item_count=1 THEN spec_item_id END,
    CASE WHEN spec_item_count=1 THEN lsl END,
    CASE WHEN spec_item_count=1 THEN usl END,
    CASE WHEN spec_item_count=1 THEN lower_operator END,
    CASE WHEN spec_item_count=1 THEN upper_operator END,
    evaluation_result,
    evaluation_reason,
    :processing_run_id,
    @evaluated_at_utc
FROM reasoned;

IF EXISTS (
    SELECT 1
    FROM test.measurement_evaluation evaluation WITH (UPDLOCK, HOLDLOCK)
    JOIN #tms_spec_evaluation_stage stage
      ON stage.measurement_id=evaluation.measurement_id
    WHERE evaluation.evaluation_type='SPEC'
      AND evaluation.evaluation_scope_key=N'FORMAL_SPEC'
      AND evaluation.is_current=1
    GROUP BY evaluation.measurement_id
    HAVING COUNT_BIG(*)>1
)
BEGIN
    RAISERROR('SPEC_EVALUATION_DUPLICATE_CURRENT', 16, 1);
    RETURN;
END;

INSERT test.measurement_evaluation(
    measurement_id,
    evaluation_type,
    evaluation_scope_key,
    spec_binding_id,
    spec_item_id,
    lsl_applied,
    usl_applied,
    lower_operator_applied,
    upper_operator_applied,
    evaluation_result,
    evaluation_reason,
    processing_run_id,
    is_current,
    evaluated_at_utc
)
SELECT
    stage.measurement_id,
    'SPEC',
    N'FORMAL_SPEC',
    stage.spec_binding_id,
    stage.spec_item_id,
    stage.lsl_applied,
    stage.usl_applied,
    stage.lower_operator_applied,
    stage.upper_operator_applied,
    stage.evaluation_result,
    stage.evaluation_reason,
    stage.processing_run_id,
    1,
    stage.evaluated_at_utc
FROM #tms_spec_evaluation_stage stage
WHERE NOT EXISTS (
    SELECT 1
    FROM test.measurement_evaluation existing WITH (UPDLOCK, HOLDLOCK)
    WHERE existing.measurement_id=stage.measurement_id
      AND existing.evaluation_type='SPEC'
      AND existing.evaluation_scope_key=N'FORMAL_SPEC'
      AND existing.is_current=1
);

IF EXISTS (
    SELECT 1
    FROM #tms_spec_evaluation_stage stage
    LEFT JOIN test.measurement_evaluation evaluation
      ON evaluation.measurement_id=stage.measurement_id
     AND evaluation.evaluation_type='SPEC'
     AND evaluation.evaluation_scope_key=N'FORMAL_SPEC'
     AND evaluation.is_current=1
    GROUP BY stage.measurement_id
    HAVING COUNT_BIG(evaluation.evaluation_id)<>1
)
BEGIN
    RAISERROR('SPEC_EVALUATION_CURRENT_INVARIANT_FAILED', 16, 1);
    RETURN;
END;

DROP TABLE #tms_spec_evaluation_stage;
DROP TABLE #tms_explicit_run_spec;
"""


def _validated_explicit_map(
    explicit_run_spec_set_ids: Mapping[int, int] | None,
) -> tuple[tuple[int, int], ...]:
    if explicit_run_spec_set_ids is None:
        return ()
    pairs: list[tuple[int, int]] = []
    for run_id, spec_set_id in explicit_run_spec_set_ids.items():
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise SpecEvaluationMaterializationError(
                "explicit run_id values must be positive integers"
            )
        if (
            isinstance(spec_set_id, bool)
            or not isinstance(spec_set_id, int)
            or spec_set_id <= 0
        ):
            raise SpecEvaluationMaterializationError(
                "explicit spec_set_id values must be positive integers"
            )
        pairs.append((run_id, spec_set_id))
    return tuple(sorted(pairs))


def materialize_processing_run_spec_evaluations(
    connection: Connection,
    *,
    processing_run_id: int,
    explicit_run_spec_set_ids: Mapping[int, int] | None = None,
) -> None:
    """Snapshot one formal Spec result per measurement in the caller transaction.

    A non-empty explicit map must cover every test run in the processing run.  It
    freezes the exact Released Spec selected by a CP/FT adapter.  With no map, the
    resolver uses event-time-active bindings and the configured scope priority.
    SQL Server ``float`` storage cannot contain NaN or infinity, so a ``MEASURED``
    value is finite once its non-NULL database value reaches this materializer.
    """

    if (
        isinstance(processing_run_id, bool)
        or not isinstance(processing_run_id, int)
        or processing_run_id <= 0
    ):
        raise SpecEvaluationMaterializationError(
            "processing_run_id must be a positive integer"
        )
    explicit_pairs = _validated_explicit_map(explicit_run_spec_set_ids)
    parameters: dict[str, int | str] = {
        "processing_run_id": processing_run_id,
        "lock_resource": f"TMS_SPEC_EVALUATION_PROCESSING_RUN:{processing_run_id}",
    }
    if explicit_pairs:
        value_rows: list[str] = []
        for index, (run_id, spec_set_id) in enumerate(explicit_pairs):
            run_key = f"explicit_run_id_{index}"
            spec_key = f"explicit_spec_set_id_{index}"
            value_rows.append(f"(:{run_key}, :{spec_key})")
            parameters[run_key] = run_id
            parameters[spec_key] = spec_set_id
        explicit_insert = (
            "INSERT #tms_explicit_run_spec(run_id,spec_set_id) VALUES\n    "
            + ",\n    ".join(value_rows)
            + ";"
        )
    else:
        explicit_insert = "-- Generic binding resolution: no explicit run/Spec map."
    connection.execute(
        text(_MATERIALIZE_SQL.format(explicit_spec_insert=explicit_insert)),
        parameters,
    )
