# TMS Windows Runtime Q0.2 Completion Report

- 日期：2026-08-27
- 里程碑：Q0.2 API/Worker/Cleanup Windows常驻运行与日志轮转部署包
- 结论：**部署代码与开发库进程验收PASS；开发机实际计划任务注册为CONDITIONAL PASS，等待管理员会话和正式服务账号**
- 数据边界：本次没有导入、清洗或删除业务数据；Cleanup只执行dry-run，Worker只在确认队列为空后执行one-shot

## 1. 完成内容

### 1.1 独立运行入口

- 新增API、Worker、Cleanup三个PowerShell运行包装器，统一验证项目目录、`.conda-env` Python和未入库的`.env.runtime.ps1`。
- 每个包装器设置独立进程角色和日志文件，返回底层Python进程退出码，便于Task Scheduler判断失败并重启。
- 提供`-ValidateOnly`无副作用检查；Worker支持`-Once`，Cleanup支持`-DryRun`。

### 1.2 Windows计划任务部署

- API和Worker：开机启动、无执行时限、每分钟重启、最多20次。
- Cleanup：每日02:00、单次上限2小时、每5分钟重试、最多3次。
- 三个任务均为`IgnoreNew`，防止同一角色并发重复实例。
- 安装默认将Cleanup注册为`DryRun`；必须显式指定`-CleanupMode Delete`才进入物理清理模式。
- 安装脚本只通过`PSCredential`接收密码；服务账号使用`Password`登录类型以访问SQL Server/共享目录，使用普通用户运行级别。
- 提供任务状态/API ready检查以及支持`-WhatIf`的卸载脚本。

### 1.3 JSONL日志轮转

- API、Worker、Cleanup分别写入`api.jsonl`、`worker.jsonl`和`cleanup.jsonl`。
- 默认每个活动文件10 MiB，保留10个备份；配置支持环境覆盖并有范围校验。
- 日志包含UTC时间、级别、进程角色、PID、Logger和消息。
- API文件日志覆盖Uvicorn生命周期、Access Log和TMS请求审计。
- `data/logs`已加入Git忽略，运行日志不会进入源码版本库。

## 2. 开发库在线验收

| 验证 | 结果 |
|---|---|
| API独立端口启动 | PASS，`127.0.0.1:18080` |
| API ready | PASS，`TMS_G0_DEV / SQL Server 12.0.5000.0 / sql2014_0013` |
| Worker one-shot | PASS；执行前确认队列仅17个SUCCESS、1个FAILED，没有QUEUED/RUNNING |
| Cleanup dry-run | PASS；发现0个到期项，删除0项 |
| 进程日志 | PASS；三类独立JSONL均生成且UTF-8内容可解析 |
| 实际计划任务注册 | 未执行；当前会话非管理员且未提供正式服务账号凭据 |

在线验收前后正式事实保持：

| 表 | 行数 |
|---|---:|
| `test.test_run` | 77 |
| `test.unit_result` | 92,335 |
| `test.measurement` | 1,588,741 |

验收结束时`ingestion.processing_job`共18条，`QUEUED/RUNNING`为0。

## 3. 自动化验证

| 验证 | 结果 |
|---|---|
| 7个PowerShell脚本语法解析 | PASS |
| 三个运行入口`-ValidateOnly` | PASS |
| 计划任务定义`-ValidateOnly` | PASS |
| Python日志轮转测试 | PASS，强制跨越1 KiB并生成备份 |
| 后端全量测试 | 110 passed，4条既有openpyxl警告 |
| 本次变更Python文件Ruff | PASS |
| API/Worker/Cleanup真实进程Smoke | PASS |

## 4. 做得好的部分

- 服务账号凭据与Git、日志、命令行参数分离，同时保留访问SQL Server和网络共享所需的无人值守登录能力。
- Cleanup以DryRun作为首次部署默认值，避免安装动作隐含扩大为立即删除。
- Task Scheduler的`IgnoreNew`与数据库Cleanup Claim共同形成进程级和数据级双层并发保护。
- 运行脚本、任务安装、状态检查和卸载均可重复执行，减少人工Task Scheduler配置漂移。
- 运行验收先只读确认队列为空，再启动Worker one-shot，没有误领取真实任务。

## 5. 不确定性与限制

- 当前Codex会话不是管理员，也没有正式服务账号；因此没有在这台开发机写入Windows Task Scheduler。不能把“定义验证通过”表述为“生产任务已安装”。
- 自动重启只覆盖进程退出；API在SQL短暂断网时通常保持运行，由连接池在后续请求恢复。仍需在目标服务器做断网、重启和24小时观察。
- 20次重启耗尽、Cleanup持续失败和磁盘告急目前通过任务状态/日志暴露，尚未接入邮件、企业微信或集中监控告警。
- API默认仅本机监听。局域网发布仍需独立的网络、认证和防火墙审批。
- 当前验证对象是开发库`TMS_G0_DEV`，不是生产数据库。

## 6. 下一步

1. 在目标Windows服务器的管理员PowerShell中，以获批服务账号先安装`CleanupMode DryRun`，完成开机重启、SQL断网恢复和至少一个每日周期验收。
2. 审批DryRun清单、生产备份/Migration窗口、工作盘容量和服务账号权限后，将Cleanup切换为`Delete`。
3. 接入任务失败与容量告警，然后进入Q1 Parquet/Arrow临时交互Workspace。
