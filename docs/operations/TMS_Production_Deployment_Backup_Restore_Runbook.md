# TMS v1.0 Core 生产部署、备份恢复与发布 Runbook

## 1. 适用范围与强制边界

本 Runbook 适用于 Windows Server、SQL Server 2014 与 Windows Task Scheduler 上的 TMS API、Route A Worker、Quick Cleanup 和 Formal Artifact Cleanup。所有数据库维护脚本默认为 `DryRun`，只有显式增加 `-Execute` 才会连接 SQL Server 并执行。

强制规则：

- 不在代码、运行配置、日志或命令行中保存 JWT、Bearer Token 或服务账号密码。
- SQL 操作仅使用当前 Windows 身份的 Integrated Security。
- 计划任务密码只通过 `Get-Credential`/`PSCredential` 交给 Task Scheduler，不作为脚本参数。
- Source 根对服务账号必须只读；Upload、Work、Quick Work 和 Log 根必须可写；各根目录不得重叠，不得经过符号链接或 reparse point。
- Restore 只允许到明确白名单且以 `_RESTORE_TEST` 或 `_DR_TEST` 结尾的测试库；目标已存在即拒绝，脚本不使用 `WITH REPLACE`。

## 2. 发布包构建与检查

在经验收的源码目录执行：

```powershell
New-Item -ItemType Directory -Force .\artifacts\releases | Out-Null
.\.conda-env\python.exe .\scripts\release\build_tms_release.py `
  --release-version v1.0-core `
  --output .\artifacts\releases\NCE-TMS-v1.0-core.zip
```

构建器按固定路径顺序、固定 ZIP 时间戳生成 `release-manifest.json`，对每个文件记录 SHA256 和字节数，执行高置信 secret scan、ZIP CRC/路径/清单检查，然后解包运行：

```powershell
.\scripts\windows\start_tms_runtime.ps1 -ValidateOnly
```

发布包禁止包含 `data/raw`、`data/workspace`、`data/work`、`quarantine`、`logs`、`.env*`、`.remember`、密钥、账号、凭据、原始数据和生成报告。两次使用相同源码与版本号构建的 ZIP SHA256 必须一致。

## 3. 生产配置与目录预检

1. 将 `docs/examples/TMS.production.runtime.example.ps1` 复制到解包根目录的 `.env.runtime.ps1`。
2. 替换所有 `__PLACEHOLDER__`，将 `TMS_EXPECTED_SCHEMA_REVISION` 设为发布清单中的唯一 Alembic head。
3. 由审批的 Windows secret bootstrap 在进程环境注入 `TMS_JWT_SECRET` 与仅有 `AUDIT_READ` 权限的 `TMS_HEALTH_BEARER_TOKEN`。如目标环境没有这一机制，停止部署，不得将秘密改写到配置或计划任务参数。
4. 所有受管目录必须由管理员事先创建。脚本不会自动创建目录或修改 ACL。

先以管理员身份做配置检查（不代替 ACL 验收）：

```powershell
.\scripts\windows\test_tms_production_preflight.ps1 -SkipAclCheck
```

再登录服务账号或在该账号的受控会话中执行真实 ACL 检查：

```powershell
.\scripts\windows\test_tms_production_preflight.ps1 `
  -ExpectedServiceAccount 'DOMAIN\svc_nce_tms'
```

只有所有输出为 `VALID`、Source 为 `READ_ONLY`、其他根为 `READ_WRITE` 才能继续。

## 4. Migration 前置、备份与后置检查

以下命令必须先不带 `-Execute` 运行并存档输出。白名单必须列出精确库名，备份路径必须是绝对 `.bak` 路径且文件不存在。

```powershell
.\scripts\windows\test_tms_migration_readiness.ps1 `
  -SqlInstance 'SQLPROD01' -Database 'NCE_TMS' `
  -AllowedDatabases 'NCE_TMS' -Phase PreMigration `
  -ExpectedSchemaRevision 'sql2014_0018'

.\scripts\windows\backup_tms_database.ps1 `
  -SqlInstance 'SQLPROD01' -Database 'NCE_TMS' `
  -AllowedDatabases 'NCE_TMS' `
  -BackupPath 'E:\SQLBackup\NCE_TMS_before_v1_0.bak'
```

变更窗口中，先停止 API/Worker，确认没有 `QUEUED/RUNNING/NEEDS_INPUT` Job 和 `STAGED` finalize intent，再重新执行上述两条命令并加 `-Execute`。备份脚本使用 `COPY_ONLY,CHECKSUM`，然后立即执行 `RESTORE VERIFYONLY ... WITH CHECKSUM`，并在结果中记录备份当时的 `SchemaRevision`，恢复演练必须使用这一精确版本做 post-check。

备份验证成功后，在同一发布目录运行：

```powershell
.\.conda-env\python.exe -m alembic -c .\db\alembic\alembic.ini upgrade head

.\scripts\windows\test_tms_migration_readiness.ps1 `
  -SqlInstance 'SQLPROD01' -Database 'NCE_TMS' `
  -AllowedDatabases 'NCE_TMS' -Phase PostMigration `
  -ExpectedSchemaRevision 'sql2014_0018' -Execute
```

Post-check 必须确认 schema revision 精确匹配，且 Dataset Current/Processing Run Current 无非 `PUBLISHED` current 和重复 current。

发布前还必须在 DBA 预先创建的、无任何用户表的专用测试库上验证从空库到 head 的完整 Migration。脚本不创建、删除或重用数据库，库名必须以 `_MIGRATION_TEST` 结尾：

```powershell
.\scripts\windows\invoke_tms_empty_database_migration_smoke.ps1 `
  -SqlInstance 'SQLUAT01' -Database 'NCE_TMS_MIGRATION_TEST' `
  -AllowedTestDatabases 'NCE_TMS_MIGRATION_TEST' `
  -ProductionDatabases 'NCE_TMS' `
  -ExpectedSchemaRevision 'sql2014_0018'
```

先审核 DryRun，再加 `-Execute`。执行时先验证库存在且 `sys.tables` 为空，再使用 Integrated Security 运行 `alembic upgrade head`，最后执行精确 schema/current consistency 检查。失败后不自动修补或删库，由 DBA 保留证据并重建新的空库再测。

## 5. 恢复演练

恢复演练只能在非生产 SQL 实例、精确测试库白名单上执行：

```powershell
.\scripts\windows\restore_tms_database.ps1 `
  -SqlInstance 'SQLUAT01' `
  -TargetDatabase 'NCE_TMS_DR_TEST' `
  -AllowedTestDatabases 'NCE_TMS_DR_TEST' `
  -ProductionDatabases 'NCE_TMS' `
  -BackupPath 'E:\SQLBackup\NCE_TMS_before_v1_0.bak' `
  -RestoreDataDirectory 'F:\SQLData\TMSRestore' `
  -ExpectedSchemaRevision 'sql2014_0018'
```

审核 DryRun 输出后才可加 `-Execute`。脚本先做 `VERIFYONLY CHECKSUM` 和 `FILELISTONLY`，为每个逻辑文件生成明确 `MOVE`目标，拒绝任何已存在的目标库/数据文件，恢复后自动执行 schema/current consistency 检查。恢复成功、查询可读且检查无异常后，将演练时间、备份 SHA256、恢复库名和检查输出归档。

## 6. 计划任务安装、健康探针与卸载

结构检查无副作用：

```powershell
.\scripts\windows\install_tms_scheduled_tasks.ps1 -ValidateOnly
```

目标服务器上的正式注册必须由管理员在审批的变更窗口执行：

```powershell
$credential = Get-Credential -Message 'TMS service account'
.\scripts\windows\install_tms_scheduled_tasks.ps1 `
  -Credential $credential -StartAfterInstall
```

安装后检查四个任务的执行文件、脚本、工作目录、`Password/Limited/IgnoreNew` 定义，并确认参数中不存在密码、Token 或 Secret。`TMS-QuickCleanup` 仅处理 `TMS_QUICK_WORK_ROOT`，`TMS-FormalCleanup` 仅处理 `TMS_WORK_ROOT/<job_id>`；两者是独立计划任务、默认都是 DryRun，不共享或合并清理根。只有在审批了各自的删除证据后，才可分别使用 `-CleanupMode Delete` 或 `-FormalCleanupMode Delete` 重新注册。

Formal Cleanup 包含两个互不抢占的阶段：先按 A5 `processing_artifact` 登记合同处理到期临时产物，再扫描 Cleaner 在登记产物前崩溃可能留下的 orphan Job root。orphan 阶段只识别 `TMS_WORK_ROOT` 的纯数字直接子目录，仅当关联 Job 为正式 Job 类型、已终态且超过 `TMS_FORMAL_ORPHAN_RETENTION_HOURS`、无有效 lease、无永久 artifact、无尚由 A5 阶段负责的活动临时 artifact 才可删除。每层目录都检查越根、symlink/reparse point、非常规文件和数量/字节上限；DryRun、删除开始、删除结果和拒绝结果均写入 `governance.audit_log`，审计不记录文件路径、lease token 或密密。首次生产启用应至少保持一个完整保留周期的 DryRun，由运维与业务负责人审批后再切换 Delete。

运行健康检查：

```powershell
.\scripts\windows\get_tms_scheduled_task_status.ps1 `
  -ExpectedUser 'DOMAIN\svc_nce_tms' -ProbeRuntime -RequireAll
```

状态复核默认同时要求 QuickCleanup 和 FormalCleanup 仍为 DryRun，模式不符时 `-RequireAll` 失败。如已经审批切换为删除模式，复核时必须显式传入 `-ExpectedCleanupMode Delete` 或 `-ExpectedFormalCleanupMode Delete`。探针要求 API `/health/ready`、Worker ready file 与已认证 Worker registry 三方的 Worker ID、Database、Server 和 Schema 一致，且 Worker 进程存在、心跳未过期。任何不一致均视为未就绪。

卸载预览与执行：

```powershell
.\scripts\windows\uninstall_tms_scheduled_tasks.ps1 -WhatIf
.\scripts\windows\uninstall_tms_scheduled_tasks.ps1
```

卸载脚本最后复核四个任务均已不存在。

## 7. 日志与回滚

API、Worker 与 Cleanup 使用经过文件名清洗的 JSONL 日志，按大小轮转，同时按 `TMS_LOG_RETENTION_DAYS` 删除仅属于本进程的过期数字轮转文件。日志 formatter 对 JWT、Bearer、Token、Password、PWD 和带密码 URL 做脱敏。

如 Migration 失败，保持 API/Worker 停止，保留错误日志和备份，不在生产库上执行 restore overwrite。由 DBA 在独立恢复库验证备份，再按变更委员会批准的数据库切换/回退方案处理。只有 post-check 和生产健康探针全部通过才能重开业务流量。

## 8. 本地无法代替的外部验收门

以下项目必须在目标环境由 DBA/系统管理员完成，本地源码测试不构成通过：

- 生产 SQL Server 的 pre-check、`BACKUP ... CHECKSUM`、`RESTORE VERIFYONLY`、Migration 与 post-check。
- 独立空测试库的 `alembic upgrade head` 全链路迁移。
- 独立测试 SQL 实例上的完整 restore drill 及恢复耗时/RPO/RTO 记录。
- 使用目标服务账号的真实 ACL 有效权限检查。
- 需要目标服务器管理员权限的 Task Scheduler 注册、重启后自启动、失败重试和卸载复核。
- 经企业审批的 Windows secret bootstrap 对计划任务进程的实际注入与轮换演练。
