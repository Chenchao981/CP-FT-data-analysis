SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* Rollback is safe only before real DOMAIN data or grants are created. */
IF EXISTS(
    SELECT 1 FROM iam.data_domain_grant
)
    THROW 50001, '0024 downgrade blocked: data-domain grants exist', 1;

IF EXISTS(
    SELECT 1 FROM ingestion.source_definition
)
    THROW 50002, '0024 downgrade blocked: source definitions exist', 1;

IF EXISTS(
    SELECT 1 FROM iam.data_domain WHERE domain_code<>N'MIGRATION_HOLD'
)
    THROW 50003, '0024 downgrade blocked: business data domains exist', 1;

IF EXISTS(
    SELECT 1 FROM dataset.dataset WHERE access_scope='DOMAIN'
)
    THROW 50004, '0024 downgrade blocked: DOMAIN datasets exist', 1;

IF EXISTS(
    SELECT 1 FROM workspace.analysis_session
    WHERE source_manifest_mode='LOCAL_PATH_SIZE_MTIME_V1'
)
    THROW 50005, '0024 downgrade blocked: LOCAL quick-analysis manifests exist', 1;
GO

ALTER TABLE workspace.analysis_session
DROP CONSTRAINT CK_analysis_session_manifest_mode;
ALTER TABLE workspace.analysis_session WITH CHECK
ADD CONSTRAINT CK_analysis_session_manifest_mode CHECK(
    source_manifest_mode IN('PATH_SIZE_MTIME_V1')
);
GO

DECLARE @migration_hold_domain_id bigint=(
    SELECT data_domain_id FROM iam.data_domain WHERE domain_code=N'MIGRATION_HOLD'
);
DECLARE @system_user_id bigint=(
    SELECT user_id FROM iam.app_user WHERE login_name=N'SYSTEM_INGESTION'
);

DROP INDEX IX_dataset_domain_access ON dataset.dataset;
DROP INDEX IX_dataset_personal_access ON dataset.dataset;
DROP INDEX IX_import_batch_domain_access ON ingestion.import_batch;
DROP INDEX IX_import_batch_personal_access ON ingestion.import_batch;

ALTER TABLE dataset.dataset DROP CONSTRAINT CK_dataset_access_binding;
ALTER TABLE ingestion.import_batch DROP CONSTRAINT CK_import_batch_access_binding;

UPDATE ingestion.import_batch
SET owner_user_id=NULL
WHERE access_scope='DOMAIN'
  AND data_domain_id=@migration_hold_domain_id
  AND owner_user_id=@system_user_id;
GO

ALTER TABLE dataset.dataset DROP CONSTRAINT CK_dataset_access_scope;
ALTER TABLE dataset.dataset DROP CONSTRAINT FK_dataset_source_definition;
ALTER TABLE dataset.dataset DROP CONSTRAINT FK_dataset_data_domain;
ALTER TABLE ingestion.import_batch DROP CONSTRAINT CK_import_batch_access_scope;
ALTER TABLE ingestion.import_batch DROP CONSTRAINT FK_import_batch_source_definition;
ALTER TABLE ingestion.import_batch DROP CONSTRAINT FK_import_batch_data_domain;
GO

ALTER TABLE dataset.dataset DROP COLUMN source_definition_id;
ALTER TABLE dataset.dataset DROP COLUMN data_domain_id;
ALTER TABLE dataset.dataset DROP COLUMN access_scope;
ALTER TABLE ingestion.import_batch DROP COLUMN source_definition_id;
ALTER TABLE ingestion.import_batch DROP COLUMN data_domain_id;
ALTER TABLE ingestion.import_batch DROP COLUMN access_scope;
GO

DROP INDEX IX_analysis_session_domain_access ON workspace.analysis_session;
ALTER TABLE workspace.analysis_session DROP CONSTRAINT CK_analysis_session_access_binding;
ALTER TABLE workspace.analysis_session DROP CONSTRAINT CK_analysis_session_access_scope;
ALTER TABLE workspace.analysis_session DROP CONSTRAINT FK_analysis_session_data_domain;
ALTER TABLE workspace.analysis_session DROP COLUMN data_domain_id;
ALTER TABLE workspace.analysis_session DROP COLUMN access_scope;
GO

DROP TABLE ingestion.source_definition;
DROP TABLE iam.data_domain_grant;
DROP TABLE iam.data_domain;
GO

DELETE rp
FROM iam.role_permission rp
JOIN iam.role r ON r.role_id=rp.role_id
JOIN iam.permission p ON p.permission_id=rp.permission_id
WHERE r.role_code IN('BUSINESS_USER','DATA_BREAK_GLASS')
   OR p.permission_code IN(
       'DATA_DOMAIN_ADMIN','SOURCE_ADMIN','SYSTEM_OPERATE','DATA_BREAK_GLASS'
   );

DELETE ur
FROM iam.user_role ur
JOIN iam.role r ON r.role_id=ur.role_id
WHERE r.role_code IN('BUSINESS_USER','DATA_BREAK_GLASS');

DELETE FROM iam.role WHERE role_code IN('BUSINESS_USER','DATA_BREAK_GLASS');
DELETE FROM iam.permission WHERE permission_code IN(
    'DATA_DOMAIN_ADMIN','SOURCE_ADMIN','SYSTEM_OPERATE','DATA_BREAK_GLASS'
);
GO

DELETE u FROM iam.app_user u
WHERE u.login_name=N'SYSTEM_INGESTION'
  AND NOT EXISTS(
      SELECT 1 FROM ingestion.import_batch b
      WHERE b.owner_user_id=u.user_id
  )
  AND NOT EXISTS(
      SELECT 1 FROM dataset.dataset d
      WHERE d.owner_user_id=u.user_id
  );
GO

SET NOCOUNT OFF;
GO
