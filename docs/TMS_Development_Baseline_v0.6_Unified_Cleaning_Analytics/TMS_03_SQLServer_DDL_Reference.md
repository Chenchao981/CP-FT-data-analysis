# TMS SQL Server DDL Reference（v0.6）

> **重要：本文件不是生产部署入口。**  
> 正式 Schema 以 `db/alembic/versions/` + `db/alembic/sql/` 为准。

## 1. v0.4 关键物理设计

```text
Rowstore
  mdm.*
  ingestion.*
  test.test_run
  test.unit_result
  test.measurement_evaluation（第一阶段）
  test.unit_bin_evaluation
  governance.audit_log

Columnstore
  test.measurement
```

## 2. measurement 变化

v0.3：

```text
measurement.is_in_spec
```

v0.4：删除该字段，保留：

```text
measurement_status
tester_pass_flag
```

业务判断：

```text
test.measurement_evaluation
```

## 3. 新增核心表

```text
mdm.scope_priority
mdm.spec_binding
mdm.bin_mapping_set

ingestion.source_file_receipt
ingestion.processing_job
ingestion.processing_run
ingestion.data_quality_rule
ingestion.data_quality_issue

test.measurement_evaluation
test.unit_bin_evaluation

governance.audit_log
```

## 4. 关键唯一约束

```text
source_file.sha256                         # 内容唯一
parser_profile(format_code, parser_version)
processing_run(source_file_id) WHERE is_current=1 AND status='PUBLISHED'
measurement_evaluation(measurement_id,evaluation_type,evaluation_scope_key)
  WHERE is_current=1
```

## 5. 匹配优先级

Priority 不硬编码在应用散落代码中，而由 `mdm.scope_priority` 受控初始化；应用 Resolver 读取后按规则执行。

## 6. 可执行 Migration 资源

见：

```text
db/alembic/sql/0001_initial_schema_v0_4.sql
db/alembic/sql/0002_unified_workflow_v0_6.sql
db/alembic/sql/0003_seed_governance_v0_6.sql
db/alembic/sql/0004_analytics_current_views_v0_6.sql
db/alembic/versions/*.py
```

由 Alembic revision 按版本链调用，不直接手工执行。`0002` 增加 IAM、Input Set、Dataset、Evaluation、Saved Analysis 与 Export；`0003` 提供可重复种子；`0004` 创建 Dataset Version 驱动的 Current Published Views。
