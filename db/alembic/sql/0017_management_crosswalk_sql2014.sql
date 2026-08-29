SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'mdm.product', N'U') IS NULL
    RAISERROR('sql2014_0017 blocked: mdm.product is missing.', 16, 1);
IF OBJECT_ID(N'mdm.supplier', N'U') IS NULL
    RAISERROR('sql2014_0017 blocked: mdm.supplier is missing.', 16, 1);
IF OBJECT_ID(N'iam.permission', N'U') IS NULL
    RAISERROR('sql2014_0017 blocked: IAM permissions are missing.', 16, 1);
GO

/*
mdm.product remains a TMS source-observed identity unless an explicit crosswalk
is approved. A raw Cleaner value must never be presented as an SAP material.
*/
IF COL_LENGTH(N'mdm.product', N'identity_class') IS NULL
BEGIN
    ALTER TABLE mdm.product ADD identity_class varchar(32) NOT NULL
        CONSTRAINT DF_product_identity_class DEFAULT('SOURCE_OBSERVED') WITH VALUES;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'mdm.product')
      AND name=N'CK_product_identity_class'
)
BEGIN
    ALTER TABLE mdm.product ADD CONSTRAINT CK_product_identity_class CHECK(
        identity_class IN('SOURCE_OBSERVED','ENTERPRISE_MAPPED')
    );
END;
GO

IF COL_LENGTH(N'mdm.supplier', N'identity_class') IS NULL
BEGIN
    ALTER TABLE mdm.supplier ADD identity_class varchar(32) NOT NULL
        CONSTRAINT DF_supplier_identity_class DEFAULT('SOURCE_CONFIGURED') WITH VALUES;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'mdm.supplier')
      AND name=N'CK_supplier_identity_class'
)
BEGIN
    ALTER TABLE mdm.supplier ADD CONSTRAINT CK_supplier_identity_class CHECK(
        identity_class IN('SOURCE_CONFIGURED','ENTERPRISE_MAPPED')
    );
END;
GO

IF OBJECT_ID(N'mdm.enterprise_product_crosswalk', N'U') IS NULL
BEGIN
    CREATE TABLE mdm.enterprise_product_crosswalk (
        crosswalk_id bigint IDENTITY(1,1) NOT NULL,
        source_system varchar(32) NOT NULL
            CONSTRAINT DF_product_crosswalk_source DEFAULT('TMS_SOURCE'),
        supplier_id bigint NOT NULL,
        test_stage varchar(16) NOT NULL,
        raw_product_code nvarchar(200) NOT NULL,
        product_id bigint NOT NULL,
        enterprise_system varchar(32) NOT NULL
            CONSTRAINT DF_product_crosswalk_enterprise DEFAULT('SAP_B1'),
        enterprise_key nvarchar(128) NULL,
        status varchar(16) NOT NULL
            CONSTRAINT DF_product_crosswalk_status DEFAULT('PENDING'),
        first_observed_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_product_crosswalk_first DEFAULT(SYSUTCDATETIME()),
        last_observed_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_product_crosswalk_last DEFAULT(SYSUTCDATETIME()),
        approved_by bigint NULL,
        approved_at_utc datetime2(3) NULL,
        decision_reason nvarchar(1000) NULL,
        row_version rowversion NOT NULL,
        CONSTRAINT PK_enterprise_product_crosswalk PRIMARY KEY CLUSTERED(crosswalk_id),
        CONSTRAINT FK_product_crosswalk_supplier FOREIGN KEY(supplier_id)
            REFERENCES mdm.supplier(supplier_id),
        CONSTRAINT FK_product_crosswalk_product FOREIGN KEY(product_id)
            REFERENCES mdm.product(product_id),
        CONSTRAINT FK_product_crosswalk_approver FOREIGN KEY(approved_by)
            REFERENCES iam.app_user(user_id),
        CONSTRAINT CK_product_crosswalk_source CHECK(source_system='TMS_SOURCE'),
        CONSTRAINT CK_product_crosswalk_stage CHECK(test_stage IN('CP','FT')),
        CONSTRAINT CK_product_crosswalk_enterprise CHECK(enterprise_system='SAP_B1'),
        CONSTRAINT CK_product_crosswalk_status CHECK(
            status IN('PENDING','APPROVED','REJECTED','RETIRED')
        ),
        CONSTRAINT CK_product_crosswalk_code CHECK(LEN(raw_product_code) BETWEEN 1 AND 200),
        CONSTRAINT CK_product_crosswalk_decision CHECK(
            (status='APPROVED' AND enterprise_key IS NOT NULL
             AND approved_by IS NOT NULL AND approved_at_utc IS NOT NULL)
            OR
            (status<>'APPROVED')
        ),
        CONSTRAINT CK_product_crosswalk_observed CHECK(
            last_observed_at_utc>=first_observed_at_utc
        )
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'mdm.enterprise_product_crosswalk')
      AND name=N'UX_product_crosswalk_source'
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UX_product_crosswalk_source
    ON mdm.enterprise_product_crosswalk(
        source_system,supplier_id,test_stage,raw_product_code
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'mdm.enterprise_product_crosswalk')
      AND name=N'IX_product_crosswalk_governance'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_product_crosswalk_governance
    ON mdm.enterprise_product_crosswalk(status,last_observed_at_utc DESC,crosswalk_id)
    INCLUDE(supplier_id,test_stage,raw_product_code,product_id,enterprise_key);
END;
GO

/* Existing formal facts become reviewable source observations, not approvals. */
INSERT mdm.enterprise_product_crosswalk(
    supplier_id,test_stage,raw_product_code,product_id,status,
    first_observed_at_utc,last_observed_at_utc,decision_reason
)
SELECT tr.supplier_id,tr.test_stage,p.product_code,tr.product_id,'PENDING',
       MIN(tr.created_at_utc),MAX(tr.created_at_utc),
       N'Existing TMS source identity; SAP mapping requires explicit approval.'
FROM test.test_run tr
JOIN mdm.product p ON p.product_id=tr.product_id
WHERE tr.test_stage IN('CP','FT')
  AND NOT EXISTS(
      SELECT 1 FROM mdm.enterprise_product_crosswalk cw
      WHERE cw.source_system='TMS_SOURCE'
        AND cw.supplier_id=tr.supplier_id
        AND cw.test_stage=tr.test_stage
        AND cw.raw_product_code=p.product_code
  )
GROUP BY tr.supplier_id,tr.test_stage,p.product_code,tr.product_id;
GO

IF NOT EXISTS(
    SELECT 1 FROM iam.permission WHERE permission_code='MANAGEMENT_READ'
)
BEGIN
    INSERT iam.permission(permission_code,description)
    VALUES(
        'MANAGEMENT_READ',
        N'读取跨批次质量KPI、趋势及受控下钻；不包含主数据审批权限'
    );
END;
GO

INSERT iam.role_permission(role_id,permission_id)
SELECT r.role_id,p.permission_id
FROM iam.role r
JOIN iam.permission p ON p.permission_code='MANAGEMENT_READ'
WHERE r.role_code IN('SYSTEM_ADMIN','DATA_ADMIN','QUALITY_ENGINEER','MANAGER_VIEWER')
  AND NOT EXISTS(
      SELECT 1 FROM iam.role_permission rp
      WHERE rp.role_id=r.role_id AND rp.permission_id=p.permission_id
  );
GO

/* Approved management/quality roles receive explicit cross-owner read scope. */
INSERT iam.data_scope_grant(
    role_id,scope_type,scope_key,permission_mode,granted_by,expires_at_utc
)
SELECT r.role_id,'GLOBAL',N'TMS_CURRENT_DATA','READ',NULL,NULL
FROM iam.role r
WHERE r.role_code IN('MANAGER_VIEWER','QUALITY_ENGINEER')
  AND NOT EXISTS(
      SELECT 1 FROM iam.data_scope_grant g
      WHERE g.role_id=r.role_id AND g.user_id IS NULL
        AND g.scope_type='GLOBAL' AND g.scope_key=N'TMS_CURRENT_DATA'
        AND g.permission_mode='READ' AND g.expires_at_utc IS NULL
  );
GO
