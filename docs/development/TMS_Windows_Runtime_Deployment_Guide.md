# TMS Windows Runtime Deployment Guide

## 1. 适用范围

本指南部署四个相互独立的TMS运行角色：

| 计划任务 | 作用 | 触发方式 | 失败策略 |
|---|---|---|---|
| `TMS-API` | FastAPI服务 | Windows开机 | 每1分钟重启，最多20次 |
| `TMS-Worker` | Route A SQL队列Worker | Windows开机 | 每1分钟重启，最多20次 |
| `TMS-QuickCleanup` | Quick Artifact TTL清理 | 每日02:00 | 每5分钟重试，最多3次 |
| `TMS-FormalCleanup` | 正式导出临时 Artifact 与孤儿 Job Root 清理 | 每日03:00 | 每5分钟重试，最多3次 |

四个任务均使用`IgnoreNew`并发策略：前一实例仍在运行时，不启动第二实例。API和Worker无执行时限；两个 Cleanup 单次最多2小时。Quick 和 Formal 使用不同受管根、不同计划任务和独立删除审批，不能合并。

## 2. 安全边界

- 安装过程通过`Get-Credential`在内存中接收服务账号密码，不把密码写入仓库、脚本、日志或命令行参数。
- Windows Task Scheduler使用操作系统保护的凭据存储，使任务可在用户未登录时访问SQL Server和获批的共享目录。
- 服务账号按普通用户权限运行，不使用管理员运行级别。安装或更新任务本身需要管理员PowerShell。
- 服务账号只应具备：读取发布目录与已注册 Source Root、执行`.conda-env` Python、修改批准的 Upload/Work/Quick Work/Log 根、连接TMS数据库所需的最小权限。Source Root 必须保持只读，各根不得重叠或经过 reparse point。
- API默认只监听`127.0.0.1:8000`。向局域网开放前，应另行审批监听地址、防火墙、反向代理和认证策略。
- QuickCleanup 和 FormalCleanup 均默认部署为`DryRun`，只预览到期对象。两者必须分别审核并明确批准后才改为`Delete`。
- 当前唯一 Schema head 为 `sql2014_0018`；部署脚本动态核对发布包 head，不能硬编码或绕过不一致。
- 生产配置不得包含 JWT、Health Token、服务账号密码或带密码数据库 URL；秘密由批准的 Windows bootstrap 注入进程环境，计划任务密码只通过 `Get-Credential` 交给 Task Scheduler。

## 3. 部署前检查

在项目根目录的普通PowerShell中执行无副作用检查：

```powershell
.\scripts\windows\run_tms_api.ps1 -ValidateOnly
.\scripts\windows\run_tms_worker.ps1 -ValidateOnly
.\scripts\windows\run_tms_cleanup.ps1 -ValidateOnly
.\scripts\windows\run_tms_formal_cleanup.ps1 -ValidateOnly
.\scripts\windows\install_tms_scheduled_tasks.ps1 -ValidateOnly
.\scripts\windows\test_tms_production_preflight.ps1 -SkipAclCheck
```

必须确认：

1. `.env.runtime.ps1`存在且由管理员维护，不纳入Git。
2. `.conda-env\python.exe`、四个运行入口和动态 Alembic head 均存在。
3. 服务账号可连接目标SQL Server，并能读取所有已注册Source Root。
4. 工作盘容量、Quick配额、TTL和每日执行时间已经审批。
5. 生产数据库已完成 pre-check、`COPY_ONLY,CHECKSUM` 备份和 `RESTORE VERIFYONLY`，并迁移至 `sql2014_0018`；正式变更前还必须在独立空测试库执行 0001→0018。
6. 以真实服务账号运行 preflight：Source 为 `READ_ONLY`，Upload/Work/Quick/Log 为 `READ_WRITE`；管理员 `-SkipAclCheck` 不能代替这一步。

## 4. 首次安装

打开“以管理员身份运行”的Windows PowerShell，在项目根目录执行：

```powershell
$credential = Get-Credential
.\scripts\windows\install_tms_scheduled_tasks.ps1 `
    -Credential $credential `
    -CleanupMode DryRun `
    -CleanupAt '02:00' `
    -FormalCleanupMode DryRun `
    -FormalCleanupAt '03:00' `
    -StartAfterInstall
```

安装完成后检查：

```powershell
.\scripts\windows\get_tms_scheduled_task_status.ps1 -ProbeRuntime -RequireAll
```

如果API不是本机`127.0.0.1:8000`，通过`-ApiUrl`传入实际ready地址。

## 5. 日志与观察期

日志写入运行配置批准的 `TMS_LOG_ROOT`：

- `api.jsonl`
- `worker.jsonl`
- `cleanup.jsonl`
- `formal-cleanup.jsonl`

每个角色按运行配置进行大小和天数双重轮转。每行是独立 UTF-8 JSON，包含 UTC 时间、级别、进程角色、PID、Logger 和消息；JWT、Bearer、Token、Password、PWD 和带密码 URL 会脱敏。

首次上线至少观察两个 Cleanup 的一个完整保留周期。重点检查任务`LastTaskResult`、API ready、Worker registry/heartbeat、两个 Cleanup dry-run 发现数量以及`BLOCKED/ERROR`结果。持续错误不能只依赖20次自动重启，应进入运维告警和人工处置。

## 6. 切换物理清理

分别审批 DryRun 结果后，使用相同服务账号凭据重新注册所批准的模式。以下示例只打开 Quick 删除，Formal 仍保持 DryRun：

```powershell
$credential = Get-Credential
.\scripts\windows\install_tms_scheduled_tasks.ps1 `
    -Credential $credential `
    -CleanupMode Delete `
    -CleanupAt '02:00' `
    -FormalCleanupMode DryRun `
    -FormalCleanupAt '03:00'
```

该操作不会立即执行 Cleanup。Formal 删除必须另行审核后显式使用 `-FormalCleanupMode Delete`；如需立即执行，应先人工运行对应入口的 DryRun 复核，再通过 Task Scheduler 启动已批准的任务。

Formal Cleanup 先按 A5 Artifact TTL 合同处理已登记临时产物，再扫描 Cleaner 在登记前崩溃可能留下的纯数字 Job Root。非终态 Job、活跃 lease、保留期内、永久 Artifact、仍活动的临时 Artifact、越根、reparse point 或扫描超限全部保留并审计。

## 7. 更新、检查与卸载

- 重复执行安装脚本会以相同任务名更新定义，不创建重复任务。
- 查看状态：`.\scripts\windows\get_tms_scheduled_task_status.ps1`
- 卸载预览：`.\scripts\windows\uninstall_tms_scheduled_tasks.ps1 -WhatIf`
- 确认卸载：`.\scripts\windows\uninstall_tms_scheduled_tasks.ps1`

卸载只停止并移除四个计划任务，不删除数据库数据、Workspace、日志、运行环境或项目文件。

## 8. 备份、恢复、发布与回滚

完整命令、白名单规则和失败处理见 `docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md`。强制顺序是：

1. 构建并检查可复现 ZIP，记录 release manifest 和 SHA-256；
2. 以目标服务账号完成生产 preflight；
3. 停止流量和 Worker，确认没有活跃 Job/STAGED intent；
4. 执行数据库 pre-check、COPY_ONLY/CHECKSUM 备份和 VERIFYONLY；
5. 在独立空测试库验证 0001→0018，再对目标库 Migration 和 post-check；
6. 注册四个计划任务，核对 API ready、Worker ready file 和 Worker registry 的 Database/Server/Schema/Worker ID；
7. 任一 post-check 或探针失败即保持流量关闭，保留备份和证据，由 DBA 在独立恢复库验证，不直接覆盖生产库。

本机已经完成脚本 DryRun、PowerShell AST、随机空库 Migration 和发布包合同验证。这些证据不等于目标 Windows Server/生产数据库的 G3/G4 已执行。
