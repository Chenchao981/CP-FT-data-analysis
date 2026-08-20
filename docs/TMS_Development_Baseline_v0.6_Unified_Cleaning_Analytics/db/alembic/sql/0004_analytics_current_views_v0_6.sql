SET XACT_ABORT ON;
GO

CREATE OR ALTER VIEW analytics.v_current_dataset_version
AS
SELECT
    d.dataset_id,
    d.dataset_code,
    d.dataset_name,
    d.dataset_type,
    d.test_stage,
    d.supplier_id,
    d.product_id,
    d.project_code,
    d.owner_user_id,
    dv.dataset_version_id,
    dv.version_no,
    dv.input_batch_id,
    dv.canonical_model_version,
    dv.row_count,
    dv.unit_count,
    dv.measurement_count,
    dv.published_by,
    dv.published_at_utc
FROM dataset.dataset d
JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id
WHERE dv.status='PUBLISHED' AND dv.is_current=1;
GO

CREATE OR ALTER VIEW analytics.v_current_test_run
AS
SELECT DISTINCT tr.*, dvr.dataset_version_id
FROM dataset.dataset_version dv
JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id
JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id
WHERE dv.status='PUBLISHED' AND dv.is_current=1;
GO

CREATE OR ALTER VIEW analytics.v_current_unit_result
AS
SELECT ur.*, ctr.dataset_version_id
FROM analytics.v_current_test_run ctr
JOIN test.unit_result ur ON ur.run_id=ctr.run_id;
GO

CREATE OR ALTER VIEW analytics.v_current_measurement
AS
SELECT m.*, cur.dataset_version_id
FROM analytics.v_current_unit_result cur
JOIN test.measurement m ON m.unit_id=cur.unit_id;
GO
