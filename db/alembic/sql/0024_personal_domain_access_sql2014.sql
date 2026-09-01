SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/*
PERSONAL / DOMAIN is the only business-data authorization boundary.
business_domain remains a classification and must never grant access.
*/

ALTER TABLE workspace.analysis_session
DROP CONSTRAINT CK_analysis_session_manifest_mode;
ALTER TABLE workspace.analysis_session WITH CHECK
ADD CONSTRAINT CK_analysis_session_manifest_mode CHECK(
    source_manifest_mode IN('PATH_SIZE_MTIME_V1','LOCAL_PATH_SIZE_MTIME_V1')
);
GO

IF NOT EXISTS(SELECT 1 FROM iam.permission WHERE permission_code='DATA_DOMAIN_ADMIN')
    INSERT iam.permission(permission_code,description)
    VALUES('DATA_DOMAIN_ADMIN',N'管理数据域及用户授权');
IF NOT EXISTS(SELECT 1 FROM iam.permission WHERE permission_code='SOURCE_ADMIN')
    INSERT iam.permission(permission_code,description)
    VALUES('SOURCE_ADMIN',N'管理受控数据源与调度入口');
IF NOT EXISTS(SELECT 1 FROM iam.permission WHERE permission_code='SYSTEM_OPERATE')
    INSERT iam.permission(permission_code,description)
    VALUES('SYSTEM_OPERATE',N'操作 Worker、清理任务和系统运行控制面');
IF NOT EXISTS(SELECT 1 FROM iam.permission WHERE permission_code='DATA_BREAK_GLASS')
    INSERT iam.permission(permission_code,description)
    VALUES('DATA_BREAK_GLASS',N'经审计的紧急数据只读突破');
GO

MERGE iam.role AS target
USING (VALUES
    ('BUSINESS_USER',N'普通业务用户')
) AS source(role_code,role_name)
ON target.role_code=source.role_code
WHEN NOT MATCHED THEN
  INSERT(role_code,role_name,active) VALUES(source.role_code,source.role_name,1);
GO

;WITH business_permissions(permission_code) AS (
    SELECT permission_code FROM (VALUES
        ('TASK_CREATE'),('TASK_RETRY'),('DATASET_READ'),
        ('ANALYSIS_RUN'),('EXPORT_DATA'),('MANAGEMENT_READ')
    ) p(permission_code)
)
INSERT iam.role_permission(role_id,permission_id)
SELECT r.role_id,p.permission_id
FROM iam.role r
JOIN business_permissions bp ON 1=1
JOIN iam.permission p ON p.permission_code=bp.permission_code
WHERE r.role_code='BUSINESS_USER'
  AND NOT EXISTS(
      SELECT 1 FROM iam.role_permission rp
      WHERE rp.role_id=r.role_id AND rp.permission_id=p.permission_id
  );
GO

/*
DATA_BREAK_GLASS is reserved but deliberately has no grantable role in this
release. It must not become usable before a reason-bound, durable audit flow
exists. Platform administrators receive control-plane functions only.
*/
INSERT iam.role_permission(role_id,permission_id)
SELECT r.role_id,p.permission_id
FROM iam.role r
JOIN iam.permission p
  ON p.permission_code IN('DATA_DOMAIN_ADMIN','SOURCE_ADMIN','SYSTEM_OPERATE')
WHERE r.role_code='SYSTEM_ADMIN'
  AND NOT EXISTS(
      SELECT 1 FROM iam.role_permission rp
      WHERE rp.role_id=r.role_id AND rp.permission_id=p.permission_id
  );
GO

IF EXISTS(
    SELECT 1
    FROM iam.role_permission rp
    JOIN iam.permission p ON p.permission_id=rp.permission_id
    WHERE p.permission_code='DATA_BREAK_GLASS'
)
    THROW 50001, 'DATA_BREAK_GLASS must not be bound to any role in this release', 1;
GO

CREATE TABLE iam.data_domain (
    data_domain_id bigint IDENTITY(1,1) NOT NULL,
    domain_code nvarchar(128) NOT NULL,
    domain_name nvarchar(200) NOT NULL,
    test_stage varchar(16) NOT NULL,
    factory_code nvarchar(64) NULL,
    active bit NOT NULL CONSTRAINT DF_data_domain_active DEFAULT(1),
    created_by_user_id bigint NULL,
    created_at_utc datetime2(3) NOT NULL
        CONSTRAINT DF_data_domain_created DEFAULT(SYSUTCDATETIME()),
    updated_at_utc datetime2(3) NOT NULL
        CONSTRAINT DF_data_domain_updated DEFAULT(SYSUTCDATETIME()),
    row_version rowversion NOT NULL,
    CONSTRAINT PK_data_domain PRIMARY KEY CLUSTERED(data_domain_id),
    CONSTRAINT UQ_data_domain_code UNIQUE(domain_code),
    CONSTRAINT FK_data_domain_creator FOREIGN KEY(created_by_user_id)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_data_domain_stage CHECK(test_stage IN('CP','FT'))
);
GO

CREATE TABLE iam.data_domain_grant (
    data_domain_grant_id bigint IDENTITY(1,1) NOT NULL,
    data_domain_id bigint NOT NULL,
    user_id bigint NOT NULL,
    status varchar(16) NOT NULL CONSTRAINT DF_data_domain_grant_status DEFAULT('ACTIVE'),
    granted_by_user_id bigint NOT NULL,
    granted_at_utc datetime2(3) NOT NULL
        CONSTRAINT DF_data_domain_grant_created DEFAULT(SYSUTCDATETIME()),
    expires_at_utc datetime2(3) NULL,
    revoked_by_user_id bigint NULL,
    revoked_at_utc datetime2(3) NULL,
    reason nvarchar(1000) NULL,
    row_version rowversion NOT NULL,
    CONSTRAINT PK_data_domain_grant PRIMARY KEY CLUSTERED(data_domain_grant_id),
    CONSTRAINT FK_data_domain_grant_domain FOREIGN KEY(data_domain_id)
        REFERENCES iam.data_domain(data_domain_id),
    CONSTRAINT FK_data_domain_grant_user FOREIGN KEY(user_id)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_data_domain_grant_granter FOREIGN KEY(granted_by_user_id)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_data_domain_grant_revoker FOREIGN KEY(revoked_by_user_id)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_data_domain_grant_status CHECK(status IN('ACTIVE','REVOKED')),
    CONSTRAINT CK_data_domain_grant_revoke CHECK(
        (status='ACTIVE' AND revoked_by_user_id IS NULL AND revoked_at_utc IS NULL)
        OR
        (status='REVOKED' AND revoked_by_user_id IS NOT NULL AND revoked_at_utc IS NOT NULL)
    ),
    CONSTRAINT CK_data_domain_grant_expiry CHECK(
        expires_at_utc IS NULL OR expires_at_utc>granted_at_utc
    )
);
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_data_domain_grant_active
ON iam.data_domain_grant(data_domain_id,user_id)
WHERE status='ACTIVE';
GO
CREATE NONCLUSTERED INDEX IX_data_domain_grant_user_read
ON iam.data_domain_grant(user_id,status,data_domain_id)
INCLUDE(expires_at_utc,granted_at_utc);
GO

/*
The technical owner keeps existing owner/published_by foreign keys intact.
It is disabled and receives no role; it is never an authorization subject.
*/
IF EXISTS(
    SELECT 1 FROM iam.app_user
    WHERE login_name=N'SYSTEM_INGESTION'
      AND (
          identity_provider<>'OIDC'
          OR ISNULL(external_subject,N'')<>N'internal:tms:system-ingestion'
          OR status<>'DISABLED'
      )
)
    THROW 50001, 'SYSTEM_INGESTION identity conflicts with an existing user', 1;

IF NOT EXISTS(SELECT 1 FROM iam.app_user WHERE login_name=N'SYSTEM_INGESTION')
BEGIN
    INSERT iam.app_user(
        login_name,display_name,identity_provider,external_subject,password_hash,status
    ) VALUES(
        N'SYSTEM_INGESTION',N'系统自动采集',
        'OIDC',N'internal:tms:system-ingestion',NULL,'DISABLED'
    );
END;
GO

DECLARE @system_user_id bigint=(
    SELECT user_id FROM iam.app_user WHERE login_name=N'SYSTEM_INGESTION'
);
IF @system_user_id IS NULL
    THROW 50001, 'SYSTEM_INGESTION user is required', 1;

IF EXISTS(
    SELECT 1 FROM iam.user_role WHERE user_id=@system_user_id
)
    THROW 50001, 'SYSTEM_INGESTION must not have application roles', 1;

INSERT iam.data_domain(
    domain_code,domain_name,test_stage,factory_code,active,created_by_user_id
)
SELECT N'MIGRATION_HOLD',N'历史数据待审核映射','CP',NULL,0,@system_user_id
WHERE NOT EXISTS(
    SELECT 1 FROM iam.data_domain WHERE domain_code=N'MIGRATION_HOLD'
);
GO

CREATE TABLE ingestion.source_definition (
    source_definition_id bigint IDENTITY(1,1) NOT NULL,
    source_code nvarchar(128) NOT NULL,
    source_name nvarchar(200) NOT NULL,
    source_kind varchar(16) NOT NULL,
    root_uri nvarchar(1000) NOT NULL,
    credential_ref nvarchar(500) NULL,
    data_domain_id bigint NOT NULL,
    service_user_id bigint NOT NULL,
    test_stage varchar(16) NOT NULL,
    factory_code nvarchar(64) NULL,
    cleaner_release_id bigint NULL,
    checkpoint_json nvarchar(max) NULL,
    active bit NOT NULL CONSTRAINT DF_source_definition_active DEFAULT(1),
    created_by_user_id bigint NULL,
    created_at_utc datetime2(3) NOT NULL
        CONSTRAINT DF_source_definition_created DEFAULT(SYSUTCDATETIME()),
    updated_at_utc datetime2(3) NOT NULL
        CONSTRAINT DF_source_definition_updated DEFAULT(SYSUTCDATETIME()),
    row_version rowversion NOT NULL,
    CONSTRAINT PK_source_definition PRIMARY KEY CLUSTERED(source_definition_id),
    CONSTRAINT UQ_source_definition_code UNIQUE(source_code),
    CONSTRAINT FK_source_definition_domain FOREIGN KEY(data_domain_id)
        REFERENCES iam.data_domain(data_domain_id),
    CONSTRAINT FK_source_definition_service_user FOREIGN KEY(service_user_id)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT FK_source_definition_cleaner FOREIGN KEY(cleaner_release_id)
        REFERENCES ingestion.cleaner_release(cleaner_release_id),
    CONSTRAINT FK_source_definition_creator FOREIGN KEY(created_by_user_id)
        REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_source_definition_kind CHECK(
        source_kind IN('FTP','SFTP','NAS','API','SAP','MANAGED')
    ),
    CONSTRAINT CK_source_definition_stage CHECK(test_stage IN('CP','FT'))
);
GO
CREATE NONCLUSTERED INDEX IX_source_definition_domain_active
ON ingestion.source_definition(data_domain_id,active,test_stage)
INCLUDE(source_code,source_name,factory_code,service_user_id);
GO

/*
Quick results inherit the authorization boundary of the place where their raw
data was read.  A Local Agent result is personal.  A managed server source is
domain data; historical server sessions cannot be mapped from this generic
migration, so they are deliberately placed in the inactive MIGRATION_HOLD
domain until an administrator performs an explicit, audited mapping.
*/
ALTER TABLE workspace.analysis_session ADD access_scope varchar(16) NULL;
ALTER TABLE workspace.analysis_session ADD data_domain_id bigint NULL;
GO

DECLARE @quick_migration_hold_domain_id bigint=(
    SELECT data_domain_id FROM iam.data_domain WHERE domain_code=N'MIGRATION_HOLD'
);

IF @quick_migration_hold_domain_id IS NULL
    THROW 50001, 'MIGRATION_HOLD data domain is required for Quick Analysis migration', 1;

UPDATE workspace.analysis_session
SET access_scope=CASE
        WHEN source_root_code=N'LOCAL_AGENT' THEN 'PERSONAL'
        ELSE 'DOMAIN'
    END,
    data_domain_id=CASE
        WHEN source_root_code=N'LOCAL_AGENT' THEN NULL
        ELSE @quick_migration_hold_domain_id
    END;
GO

IF EXISTS(
    SELECT 1 FROM workspace.analysis_session
    WHERE access_scope IS NULL
       OR (source_root_code=N'LOCAL_AGENT'
           AND (access_scope<>'PERSONAL' OR data_domain_id IS NOT NULL))
       OR (source_root_code<>N'LOCAL_AGENT'
           AND (access_scope<>'DOMAIN' OR data_domain_id IS NULL))
)
    THROW 50001, 'Quick Analysis access-scope backfill failed closed', 1;
GO

ALTER TABLE workspace.analysis_session
ALTER COLUMN access_scope varchar(16) NOT NULL;
ALTER TABLE workspace.analysis_session ADD CONSTRAINT FK_analysis_session_data_domain
    FOREIGN KEY(data_domain_id) REFERENCES iam.data_domain(data_domain_id);
ALTER TABLE workspace.analysis_session ADD CONSTRAINT CK_analysis_session_access_scope
    CHECK(access_scope IN('PERSONAL','DOMAIN'));
ALTER TABLE workspace.analysis_session ADD CONSTRAINT CK_analysis_session_access_binding
    CHECK(
        (source_root_code=N'LOCAL_AGENT' AND access_scope='PERSONAL'
            AND data_domain_id IS NULL)
        OR
        (source_root_code<>N'LOCAL_AGENT' AND access_scope='DOMAIN'
            AND data_domain_id IS NOT NULL)
    );
GO

CREATE NONCLUSTERED INDEX IX_analysis_session_domain_access
ON workspace.analysis_session(data_domain_id,created_at_utc DESC,analysis_session_id DESC)
INCLUDE(access_scope,owner_user_id,status,analysis_type,test_stage,factory_code,
        source_file_count,source_total_bytes,expires_at_utc);
GO

ALTER TABLE ingestion.import_batch ADD access_scope varchar(16) NULL;
ALTER TABLE ingestion.import_batch ADD data_domain_id bigint NULL;
ALTER TABLE ingestion.import_batch ADD source_definition_id bigint NULL;
ALTER TABLE dataset.dataset ADD access_scope varchar(16) NULL;
ALTER TABLE dataset.dataset ADD data_domain_id bigint NULL;
ALTER TABLE dataset.dataset ADD source_definition_id bigint NULL;
GO

DECLARE @migration_system_user_id bigint=(
    SELECT user_id FROM iam.app_user WHERE login_name=N'SYSTEM_INGESTION'
);
DECLARE @migration_hold_domain_id bigint=(
    SELECT data_domain_id FROM iam.data_domain WHERE domain_code=N'MIGRATION_HOLD'
);

/* Existing owned rows become PERSONAL, including historical PRODUCTION rows. */
UPDATE ingestion.import_batch
SET access_scope='PERSONAL',data_domain_id=NULL,source_definition_id=NULL
WHERE owner_user_id IS NOT NULL;

/* Ownerless history is denied through an inactive domain with no grants. */
UPDATE ingestion.import_batch
SET owner_user_id=@migration_system_user_id,
    access_scope='DOMAIN',
    data_domain_id=@migration_hold_domain_id,
    source_definition_id=NULL
WHERE owner_user_id IS NULL;

/*
Only a Dataset whose complete historical Batch lineage belongs to the same
human owner may become PERSONAL.  Any ownerless, cross-owner, DOMAIN or mixed
lineage is quarantined instead of guessing ownership from dataset.owner_user_id.
Datasets with no versions contain no imported data and retain their owner.
*/
;WITH dataset_acl_classification AS (
    SELECT d.dataset_id,
           CASE WHEN EXISTS(
               SELECT 1
               FROM dataset.dataset_version dv
               JOIN ingestion.import_batch b
                 ON b.import_batch_id=dv.input_batch_id
               WHERE dv.dataset_id=d.dataset_id
                 AND (
                     b.access_scope<>'PERSONAL'
                     OR b.owner_user_id<>d.owner_user_id
                     OR b.data_domain_id IS NOT NULL
                     OR b.source_definition_id IS NOT NULL
                 )
           ) THEN 1 ELSE 0 END AS requires_migration_hold
    FROM dataset.dataset d
)
UPDATE d
SET owner_user_id=CASE WHEN c.requires_migration_hold=1
                       THEN @migration_system_user_id ELSE d.owner_user_id END,
    access_scope=CASE WHEN c.requires_migration_hold=1
                      THEN 'DOMAIN' ELSE 'PERSONAL' END,
    data_domain_id=CASE WHEN c.requires_migration_hold=1
                        THEN @migration_hold_domain_id ELSE NULL END,
    source_definition_id=NULL
FROM dataset.dataset d
JOIN dataset_acl_classification c ON c.dataset_id=d.dataset_id;

IF EXISTS(
    SELECT 1
    FROM dataset.dataset d
    JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id
    JOIN ingestion.import_batch b ON b.import_batch_id=dv.input_batch_id
    WHERE d.access_scope='PERSONAL'
      AND (
          b.access_scope<>'PERSONAL'
          OR b.owner_user_id<>d.owner_user_id
          OR b.data_domain_id IS NOT NULL
          OR b.source_definition_id IS NOT NULL
      )
)
    THROW 50001, 'PERSONAL Dataset history has inconsistent Batch ownership', 1;
GO

ALTER TABLE ingestion.import_batch ALTER COLUMN access_scope varchar(16) NOT NULL;
ALTER TABLE dataset.dataset ALTER COLUMN access_scope varchar(16) NOT NULL;
GO

ALTER TABLE ingestion.import_batch ADD CONSTRAINT FK_import_batch_data_domain
    FOREIGN KEY(data_domain_id) REFERENCES iam.data_domain(data_domain_id);
ALTER TABLE ingestion.import_batch ADD CONSTRAINT FK_import_batch_source_definition
    FOREIGN KEY(source_definition_id)
    REFERENCES ingestion.source_definition(source_definition_id);
ALTER TABLE ingestion.import_batch ADD CONSTRAINT CK_import_batch_access_scope
    CHECK(access_scope IN('PERSONAL','DOMAIN'));
ALTER TABLE ingestion.import_batch ADD CONSTRAINT CK_import_batch_access_binding
    CHECK(
        (access_scope='PERSONAL' AND owner_user_id IS NOT NULL
            AND data_domain_id IS NULL AND source_definition_id IS NULL)
        OR
        (access_scope='DOMAIN' AND owner_user_id IS NOT NULL
            AND data_domain_id IS NOT NULL)
    );
GO

ALTER TABLE dataset.dataset ADD CONSTRAINT FK_dataset_data_domain
    FOREIGN KEY(data_domain_id) REFERENCES iam.data_domain(data_domain_id);
ALTER TABLE dataset.dataset ADD CONSTRAINT FK_dataset_source_definition
    FOREIGN KEY(source_definition_id)
    REFERENCES ingestion.source_definition(source_definition_id);
ALTER TABLE dataset.dataset ADD CONSTRAINT CK_dataset_access_scope
    CHECK(access_scope IN('PERSONAL','DOMAIN'));
ALTER TABLE dataset.dataset ADD CONSTRAINT CK_dataset_access_binding
    CHECK(
        (access_scope='PERSONAL' AND owner_user_id IS NOT NULL
            AND data_domain_id IS NULL AND source_definition_id IS NULL)
        OR
        (access_scope='DOMAIN' AND owner_user_id IS NOT NULL
            AND data_domain_id IS NOT NULL)
    );
GO

CREATE NONCLUSTERED INDEX IX_import_batch_personal_access
ON ingestion.import_batch(access_scope,owner_user_id,started_at_utc DESC)
INCLUDE(import_batch_id,data_domain_id,status,test_stage,factory_code);
GO
CREATE NONCLUSTERED INDEX IX_import_batch_domain_access
ON ingestion.import_batch(data_domain_id,started_at_utc DESC)
INCLUDE(import_batch_id,access_scope,owner_user_id,status,test_stage,factory_code);
GO
CREATE NONCLUSTERED INDEX IX_dataset_personal_access
ON dataset.dataset(access_scope,owner_user_id,dataset_id DESC)
INCLUDE(data_domain_id,test_stage,lifecycle_status);
GO
CREATE NONCLUSTERED INDEX IX_dataset_domain_access
ON dataset.dataset(data_domain_id,dataset_id DESC)
INCLUDE(access_scope,owner_user_id,test_stage,lifecycle_status);
GO

SET NOCOUNT OFF;
GO
