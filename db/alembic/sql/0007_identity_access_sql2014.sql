SET XACT_ABORT ON;

ALTER TABLE iam.app_user ADD email nvarchar(256) NULL;
ALTER TABLE iam.app_user ADD department_code nvarchar(128) NULL;
ALTER TABLE iam.app_user ADD last_login_at_utc datetime2(3) NULL;
ALTER TABLE iam.app_user ADD failed_login_count int NOT NULL CONSTRAINT DF_app_user_failed_login DEFAULT(0);
ALTER TABLE iam.app_user ADD locked_until_utc datetime2(3) NULL;
GO

ALTER TABLE iam.app_user DROP CONSTRAINT CK_app_user_status;
ALTER TABLE iam.app_user ADD CONSTRAINT CK_app_user_status CHECK(status IN('PENDING','ACTIVE','LOCKED','DISABLED'));
GO

CREATE UNIQUE NONCLUSTERED INDEX UX_app_user_email
ON iam.app_user(email) WHERE email IS NOT NULL;
GO

CREATE TABLE iam.auth_session (
    session_id bigint IDENTITY(1,1) NOT NULL,
    user_id bigint NOT NULL,
    token_jti uniqueidentifier NOT NULL,
    issued_at_utc datetime2(3) NOT NULL CONSTRAINT DF_auth_session_issued DEFAULT(SYSUTCDATETIME()),
    expires_at_utc datetime2(3) NOT NULL,
    revoked_at_utc datetime2(3) NULL,
    client_ip nvarchar(64) NULL,
    user_agent nvarchar(500) NULL,
    CONSTRAINT PK_auth_session PRIMARY KEY CLUSTERED(session_id),
    CONSTRAINT UQ_auth_session_jti UNIQUE(token_jti),
    CONSTRAINT FK_auth_session_user FOREIGN KEY(user_id) REFERENCES iam.app_user(user_id)
);
GO
CREATE NONCLUSTERED INDEX IX_auth_session_user_active
ON iam.auth_session(user_id,expires_at_utc) INCLUDE(token_jti,revoked_at_utc);
GO

CREATE TABLE iam.login_audit (
    login_audit_id bigint IDENTITY(1,1) NOT NULL,
    login_name nvarchar(128) NOT NULL,
    user_id bigint NULL,
    outcome varchar(24) NOT NULL,
    client_ip nvarchar(64) NULL,
    user_agent nvarchar(500) NULL,
    occurred_at_utc datetime2(3) NOT NULL CONSTRAINT DF_login_audit_time DEFAULT(SYSUTCDATETIME()),
    CONSTRAINT PK_login_audit PRIMARY KEY CLUSTERED(login_audit_id),
    CONSTRAINT FK_login_audit_user FOREIGN KEY(user_id) REFERENCES iam.app_user(user_id),
    CONSTRAINT CK_login_audit_outcome CHECK(outcome IN('SUCCESS','BAD_PASSWORD','NOT_ACTIVE','NOT_FOUND','LOCKED'))
);
GO

INSERT iam.role_permission(role_id,permission_id)
SELECT r.role_id,p.permission_id
FROM iam.role r CROSS JOIN iam.permission p
WHERE r.role_code='SYSTEM_ADMIN'
AND NOT EXISTS (
    SELECT 1 FROM iam.role_permission rp
    WHERE rp.role_id=r.role_id AND rp.permission_id=p.permission_id
);
GO

INSERT iam.data_scope_grant(role_id,scope_type,scope_key,permission_mode)
SELECT r.role_id,'GLOBAL','*',m.permission_mode
FROM iam.role r
CROSS JOIN (VALUES('READ'),('WRITE'),('GOVERN'),('EXPORT')) m(permission_mode)
WHERE r.role_code='SYSTEM_ADMIN'
AND NOT EXISTS (
    SELECT 1 FROM iam.data_scope_grant dsg
    WHERE dsg.role_id=r.role_id AND dsg.scope_type='GLOBAL'
      AND dsg.scope_key='*' AND dsg.permission_mode=m.permission_mode
);
GO
