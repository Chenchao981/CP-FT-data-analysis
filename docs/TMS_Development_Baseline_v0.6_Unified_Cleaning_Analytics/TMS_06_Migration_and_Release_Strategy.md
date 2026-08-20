# TMS v0.6 数据库 Migration 与 Release Strategy

## 1. 唯一生产入口

正式 Schema 变更采用 Alembic revision + SQLAlchemy Connection + SQL Server Native T-SQL。Markdown 中的 SQL 仅用于说明；`db/alembic/versions/` revision 链是执行入口，`alembic_version` 是数据库 Schema 当前版本的唯一事实来源。

## 2. 当前 revision 链

```text
20260820_0001_initial_schema_v0_4
→ 20260820_0002_unified_workflow_v0_6
→ 20260820_0003_seed_governance_v0_6
→ 20260820_0004_analytics_current_views_v0_6
```

对应目录：

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
8. 每个 revision 写明 SQL Server 2022+ 兼容性和 Edition 限制。

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

`0001` 保留 v0.4 Canonical 表作为历史基础；`0002` 增加 v0.6 应用闭环；`0003` 提供可重复执行的治理种子；`0004` 创建默认 Current Published Views。后续从 `0005` 开始只做增量 migration。

`0002` 为兼容历史数据，将 `measurement_evaluation.evaluation_run_id` 初始建为可空。应用上线后新写入必须非空；完成历史回填和核对后，以新的 revision 收紧数据库 `NOT NULL`。`0003` 只建立治理主数据和权限基线，不替代业务 Owner 对 DQ Rule Version、Bin/Spec/PAT Rule Version 的批准。

如果尚未有任何共享数据库，可在正式开发前评审是否 squash 为新的单一 `0001_v0_6`。一旦任一共享 DEV/TEST 数据库使用当前链，禁止改写历史 revision。

## 7. 本文档包的验证边界

本目录提供 revision 与 T-SQL 参考实现，可做 Python 静态检查和目录一致性检查。只有在可用的 SQL Server 2022+ 测试实例上完成 `upgrade head`、约束/View 查询和回滚演练后，才能把 Migration 状态标记为“已执行验收”。
