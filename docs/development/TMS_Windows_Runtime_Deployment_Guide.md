# TMS Windows Runtime Deployment Guide

## 1. 适用范围

本指南部署三个相互独立的TMS运行角色：

| 计划任务 | 作用 | 触发方式 | 失败策略 |
|---|---|---|---|
| `TMS-API` | FastAPI服务 | Windows开机 | 每1分钟重启，最多20次 |
| `TMS-Worker` | Route A SQL队列Worker | Windows开机 | 每1分钟重启，最多20次 |
| `TMS-QuickCleanup` | Quick Artifact TTL清理 | 每日02:00 | 每5分钟重试，最多3次 |

三个任务均使用`IgnoreNew`并发策略：前一实例仍在运行时，不启动第二实例。API和Worker无执行时限；Cleanup单次最多2小时。

## 2. 安全边界

- 安装过程通过`Get-Credential`在内存中接收服务账号密码，不把密码写入仓库、脚本、日志或命令行参数。
- Windows Task Scheduler使用操作系统保护的凭据存储，使任务可在用户未登录时访问SQL Server和获批的共享目录。
- 服务账号按普通用户权限运行，不使用管理员运行级别。安装或更新任务本身需要管理员PowerShell。
- 服务账号只应具备：读取程序与已注册Source Root、执行`.conda-env` Python、修改`data/logs`和`data/workspace`、连接TMS数据库所需的最小权限。
- API默认只监听`127.0.0.1:8000`。向局域网开放前，应另行审批监听地址、防火墙、反向代理和认证策略。
- Cleanup默认部署为`DryRun`，只预览到期对象。明确批准后才改为`Delete`。

## 3. 部署前检查

在项目根目录的普通PowerShell中执行无副作用检查：

```powershell
.\scripts\windows\run_tms_api.ps1 -ValidateOnly
.\scripts\windows\run_tms_worker.ps1 -ValidateOnly
.\scripts\windows\run_tms_cleanup.ps1 -ValidateOnly
.\scripts\windows\install_tms_scheduled_tasks.ps1 -ValidateOnly
```

必须确认：

1. `.env.runtime.ps1`存在且由管理员维护，不纳入Git。
2. `.conda-env\python.exe`及三个入口文件均存在。
3. 服务账号可连接目标SQL Server，并能读取所有已注册Source Root。
4. 工作盘容量、Quick配额、TTL和每日执行时间已经审批。
5. 生产数据库已备份并迁移至要求的Schema Revision。

## 4. 首次安装

打开“以管理员身份运行”的Windows PowerShell，在项目根目录执行：

```powershell
$credential = Get-Credential
.\scripts\windows\install_tms_scheduled_tasks.ps1 `
    -Credential $credential `
    -CleanupMode DryRun `
    -CleanupAt '02:00' `
    -StartAfterInstall
```

安装完成后检查：

```powershell
.\scripts\windows\get_tms_scheduled_task_status.ps1 -ProbeApi -RequireAll
```

如果API不是本机`127.0.0.1:8000`，通过`-ApiUrl`传入实际ready地址。

## 5. 日志与观察期

日志默认写入`data/logs`：

- `api.jsonl`
- `worker.jsonl`
- `cleanup.jsonl`

每个角色默认单文件10 MiB、保留10个备份，三类进程理论上限约330 MiB。每行是独立UTF-8 JSON，包含UTC时间、级别、进程角色、PID、Logger和消息。

首次上线至少观察一个完整Cleanup周期。重点检查任务`LastTaskResult`、API ready、Worker异常、Cleanup dry-run发现数量以及`BLOCKED/ERROR`结果。持续错误不能只依赖20次自动重启，应进入运维告警和人工处置。

## 6. 切换物理清理

审批DryRun结果后，使用相同服务账号凭据重新注册，仅将模式改为`Delete`：

```powershell
$credential = Get-Credential
.\scripts\windows\install_tms_scheduled_tasks.ps1 `
    -Credential $credential `
    -CleanupMode Delete `
    -CleanupAt '02:00'
```

该操作不会立即执行Cleanup；如需立即执行，应先人工运行`run_tms_cleanup.ps1 -DryRun`复核，再通过Task Scheduler启动已批准的任务。

## 7. 更新、检查与卸载

- 重复执行安装脚本会以相同任务名更新定义，不创建重复任务。
- 查看状态：`.\scripts\windows\get_tms_scheduled_task_status.ps1`
- 卸载预览：`.\scripts\windows\uninstall_tms_scheduled_tasks.ps1 -WhatIf`
- 确认卸载：`.\scripts\windows\uninstall_tms_scheduled_tasks.ps1`

卸载只停止并移除三个计划任务，不删除数据库数据、Workspace、日志、运行环境或项目文件。
