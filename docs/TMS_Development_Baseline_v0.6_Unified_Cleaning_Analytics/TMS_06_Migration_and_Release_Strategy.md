# TMS v0.6 数据库 Migration 与 Release Strategy

## 1. 生产入口状态

正式Schema变更采用Alembic revision + SQLAlchemy Connection + SQL Server Native T-SQL。根据ADR-0001，首版目标为SQL Server 2014；仓库根目录 `db/alembic/` 的 `sql2014_0001 → sql2014_0004` 是当前唯一执行入口，已在隔离数据库完成空库升级。本文档目录中的 `0001 → 0004` 是2022+设计参考，不得用于目标实例；`alembic_version` 是数据库Schema当前版本的唯一事实来源。

## 2. 正式SQL Server 2014 revision链

```text
sql2014_0001_core_schema
→ sql2014_0002_unified_workflow
→ sql2014_0003_governance_seed
→ sql2014_0004_analytics_views
```

执行目录为仓库根目录 `db/alembic/`。以下原链只保留在本文档包中作设计参考，禁止在2014执行：

```text
20260820_0001_initial_schema_v0_4
→ 20260820_0002_unified_workflow_v0_6
→ 20260820_0003_seed_governance_v0_6
→ 20260820_0004_analytics_current_views_v0_6
```

2022+参考链对应本文档包内目录：

```text
db/alembic/
├─ env.py
├─ sql/
│  ├─ 0001_initial_schema_v0_4.sql
│  ├─ 0002_unified_workflow_v0_6.sql
│  ├─ 0003_seed_governance_v0_6.sql
│  └─ 0004_analytics_current_views_v0_6.sql
└─ versions/
   ├─ 20260820_0001_initial_schema_v0_4.py
   ├─ 20260820_0002_unified_workflow_v0_6.py
   ├─ 20260820_0003_seed_governance_v0_6.py
   └─ 20260820_0004_analytics_current_views_v0_6.py
```

SQL 文件中的 `GO` 由 revision helper 按独立批次拆分；不能把包含 `GO` 的整份文件直接交给 SQL Server Driver。

当前 revision 使用 Native T-SQL，因此只支持连接目标 SQL Server 的 online migration；`alembic upgrade --sql` 离线生成模式会明确失败，避免把未拆分或语义不完整的脚本误用于生产。

## 3. Migration 原则

1. Revision 进入共享分支后不修改历史内容；修复使用新 revision。
2. DEV/TEST/PROD 按同一 revision 链升级。
3. 禁止在 PROD 手工加列/索引后不补 migration。
4. 生产 migration 前备份并记录 restore point。
5. 大表变更评估锁、日志、磁盘和执行时间。
6. Schema、种子数据和 View 分 revision。
7. 高风险 destructive change 不自动 downgrade；回滚优先应用回滚 + restore/forward-fix。
8. 每个正式 revision 必须通过SQL Server 2014 SP3、Compatibility Level 120和实际Edition验证。
9. SQL Server 2014兼容链不使用 `ISJSON`、`CREATE OR ALTER`，Measurement不建立Clustered Columnstore。

## 4. 发布流程

```text
Create Revision
→ Static Validation
→ Empty SQL Server: upgrade base → head
→ Previous Release DB: upgrade previous → head
→ Seed/View/Constraint Smoke Test
→ Parser Golden Sample Reconciliation
→ Authorization and Export Smoke Test
→ Test Environment
→ Backup Production
→ alembic upgrade <approved revision>
→ Application Deploy
→ Post-deploy Smoke Test
```

## 5. CI 最低验收

- 空数据库升级到 head；
- 上一 release 升级到 head；
- revision 链单头、无分叉；
- 预期 Schema/Table/Index/View 存在；
- Input Set、Processing、Dataset Publish 能完成一条事务；
- DQ BLOCKER 无法 Waive 或发布；
- Evaluation Run 固定 Rule/Dataset/Filter；
- RBAC 与对象级授权拒绝越权读取；
- Export Job 记录 Dataset/Filter/Artifact Hash；
- Current Published Views 只返回 current Dataset Version；
- 华虹 CP 与日月新 FT 黄金样例按批准口径对账。

## 6. v0.6 Initial Baseline 说明

参考链中，`0001` 保留 v0.4 Canonical 表，`0002` 增加 v0.6 应用闭环，`0003` 提供治理种子，`0004` 创建Current Published Views。该链未在共享数据库执行，可以保留作2022+设计参考；SQL Server 2014正式链使用独立revision ID，禁止与参考链混用。

`0002` 为兼容历史数据，将 `measurement_evaluation.evaluation_run_id` 初始建为可空。应用上线后新写入必须非空；完成历史回填和核对后，以新的 revision 收紧数据库 `NOT NULL`。`0003` 只建立治理主数据和权限基线，不替代业务 Owner 对 DQ Rule Version、Bin/Spec/PAT Rule Version 的批准。

SQL Server 2014正式链从 `sql2014_0001` 开始，已完成JSON约束替代、Rowstore Measurement、2014 View DDL和种子/View Smoke Test。一旦任一共享DEV/TEST数据库使用正式链，禁止改写历史revision。

## 7. 本文档包的验证边界

SQL Server 2014兼容链已在隔离数据库完成 `upgrade head`、Schema/View/种子和Rowstore索引验证，可标记为“开发空库验证通过”。黄金样例、性能、备份恢复和生产发布尚未验收，不能标记为生产就绪。
