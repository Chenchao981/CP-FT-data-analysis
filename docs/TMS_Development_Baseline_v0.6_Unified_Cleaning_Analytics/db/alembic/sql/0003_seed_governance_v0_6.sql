SET XACT_ABORT ON;

MERGE mdm.scope_priority AS target
USING (VALUES
    ('EXPLICIT_OVERRIDE',600,N'明确人工覆盖'),
    ('CUSTOMER_PRODUCT_PROGRAM',500,N'客户+产品+程序'),
    ('CUSTOMER_PRODUCT',450,N'客户+产品'),
    ('PRODUCT_PROGRAM',400,N'产品+程序'),
    ('PRODUCT_SUPPLIER_STAGE',350,N'产品+供应商+阶段'),
    ('PRODUCT_STAGE',300,N'产品+阶段'),
    ('PRODUCT',200,N'产品'),
    ('GLOBAL',100,N'全局')
) AS src(scope_code,priority,description)
ON target.scope_code=src.scope_code
WHEN NOT MATCHED THEN
  INSERT(scope_code,priority,description,active) VALUES(src.scope_code,src.priority,src.description,1);
GO

MERGE iam.permission AS target
USING (VALUES
    ('TASK_CREATE',N'创建清洗任务'),
    ('TASK_RETRY',N'重试清洗任务'),
    ('DATASET_READ',N'读取授权数据集'),
    ('DATASET_PUBLISH',N'发布数据集版本'),
    ('ANALYSIS_RUN',N'运行分析评价'),
    ('EXPORT_DATA',N'导出授权数据'),
    ('FORMAT_GOVERN',N'维护格式档案和清洗器'),
    ('RULE_GOVERN',N'维护规格、Bin、DQ 和评价规则'),
    ('DQ_WAIVE_ERROR',N'豁免允许豁免的 ERROR'),
    ('AUDIT_READ',N'读取审计日志'),
    ('USER_ADMIN',N'管理用户与角色')
) AS src(permission_code,description)
ON target.permission_code=src.permission_code
WHEN NOT MATCHED THEN
  INSERT(permission_code,description) VALUES(src.permission_code,src.description);
GO

MERGE iam.role AS target
USING (VALUES
    ('SYSTEM_ADMIN',N'系统管理员'),
    ('DATA_ADMIN',N'数据管理员'),
    ('CP_ENGINEER',N'CP工程师'),
    ('FT_ENGINEER',N'FT工程师'),
    ('QUALITY_ENGINEER',N'质量工艺'),
    ('MANAGER_VIEWER',N'管理只读'),
    ('AUDITOR',N'审计与IT运维')
) AS src(role_code,role_name)
ON target.role_code=src.role_code
WHEN NOT MATCHED THEN
  INSERT(role_code,role_name,active) VALUES(src.role_code,src.role_name,1);
GO

;WITH grants(role_code,permission_code) AS (
    SELECT * FROM (VALUES
      ('SYSTEM_ADMIN','USER_ADMIN'),('SYSTEM_ADMIN','AUDIT_READ'),
      ('DATA_ADMIN','TASK_CREATE'),('DATA_ADMIN','TASK_RETRY'),('DATA_ADMIN','DATASET_READ'),
      ('DATA_ADMIN','DATASET_PUBLISH'),('DATA_ADMIN','FORMAT_GOVERN'),('DATA_ADMIN','RULE_GOVERN'),
      ('DATA_ADMIN','DQ_WAIVE_ERROR'),('DATA_ADMIN','ANALYSIS_RUN'),
      ('CP_ENGINEER','TASK_CREATE'),('CP_ENGINEER','DATASET_READ'),('CP_ENGINEER','ANALYSIS_RUN'),('CP_ENGINEER','EXPORT_DATA'),
      ('FT_ENGINEER','TASK_CREATE'),('FT_ENGINEER','DATASET_READ'),('FT_ENGINEER','ANALYSIS_RUN'),('FT_ENGINEER','EXPORT_DATA'),
      ('QUALITY_ENGINEER','DATASET_READ'),('QUALITY_ENGINEER','ANALYSIS_RUN'),('QUALITY_ENGINEER','EXPORT_DATA'),
      ('MANAGER_VIEWER','DATASET_READ'),
      ('AUDITOR','AUDIT_READ')
    ) v(role_code,permission_code)
)
INSERT iam.role_permission(role_id,permission_id)
SELECT r.role_id,p.permission_id
FROM grants g
JOIN iam.role r ON r.role_code=g.role_code
JOIN iam.permission p ON p.permission_code=g.permission_code
WHERE NOT EXISTS (
  SELECT 1 FROM iam.role_permission rp
  WHERE rp.role_id=r.role_id AND rp.permission_id=p.permission_id
);
GO

MERGE ingestion.data_quality_rule AS target
USING (VALUES
    ('DQ_UNKNOWN_FORMAT',N'未知或歧义格式','BLOCKER',1,N'格式档案无法唯一匹配'),
    ('DQ_MIXED_FORMAT_VERSION',N'输入集合混合格式版本','BLOCKER',1,N'同一任务出现不兼容格式版本'),
    ('DQ_UNKNOWN_IDENTITY',N'关键业务身份缺失','BLOCKER',1,N'产品、Lot、Wafer 或测试批次无法确定'),
    ('DQ_UNKNOWN_UNIT',N'参数单位未知','BLOCKER',1,N'无法确定单位或换算规则'),
    ('DQ_SPEC_AMBIGUOUS',N'规格匹配歧义','BLOCKER',1,N'最高优先级存在多条匹配'),
    ('DQ_BIN_RULE_UNKNOWN',N'Bin/Pass 规则未知','BLOCKER',1,N'无法确定 Pass Bin 或 Bin Mapping'),
    ('DQ_ROW_RECONCILIATION',N'源输出行数对账失败','ERROR',1,N'源行和输出行不满足批准合同'),
    ('DQ_UNKNOWN_TIMEZONE',N'源时区未知','WARNING',0,N'源文件没有明确时区')
) AS src(rule_code,rule_name,default_severity,is_blocking,description)
ON target.rule_code=src.rule_code
WHEN NOT MATCHED THEN
  INSERT(rule_code,rule_name,default_severity,is_blocking,description,active)
  VALUES(src.rule_code,src.rule_name,src.default_severity,src.is_blocking,src.description,1);
GO
