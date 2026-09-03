# TMS Quick Workspace Lifecycle Q0.1 Completion Report

- 日期：2026-08-27
- 里程碑：Q0.1 Quick Analysis容量保护与过期结果物理清理
- 结论：**代码、SQL Server 2014 Migration、容量事务锁和合成过期Artifact在线清理全部通过；开发库状态为 PASS**
- 数据边界：只处理Quick Analysis临时工作目录和临时Artifact元数据；不删除Session、Job、Manifest、SHA或正式`test.*`事实

## 1. 完成内容

### 1.1 容量预留与配额

- 新增`QuickCapacityPolicy`，PAT任务按`源文件总字节数 × reserve_ratio + overhead`估算峰值临时磁盘。
- 默认预留比例50%，固定开销64 MiB；520文件、3,041,085,645 bytes的实算预留为1,587,651,687 bytes（约1.48 GiB）。
- 创建任务前检查工作盘实际剩余空间，并保留默认10 GiB安全余量。
- SQL事务内使用`sp_getapplock`锁定容量检查，防止并发创建绕过配额。
- 全局配额默认100 GiB，单用户配额默认20 GiB；计算口径为“活跃任务预留 + 失败任务未清理预留 + 尚为PRESENT/BLOCKED/ERROR的Quick Artifact”。
- 超限时失败关闭，不创建Session和Job；全局、用户、磁盘不足使用不同错误代码。

### 1.2 可审计物理清理

- 新增`sql2014_0013`：Session记录预留字节、清理状态、尝试次数、时间和错误；Artifact记录物理状态、删除尝试、时间和错误。
- 清理状态为`RETAINED/CLEANING/CLEANED/BLOCKED/ERROR`；物理状态为`PRESENT/DELETED/MISSING/BLOCKED/ERROR`。
- 清理器只选择TTL到期且Job已为`SUCCESS/FAILED/CANCELLED`的Quick Analysis，运行中的任务不会被清理。
- 删除目标必须是`TMS_QUICK_WORK_ROOT/<job_id>`的精确子目录；Artifact路径必须位于该目录内。
- 发现路径逃逸、盘符切换、非目录Job Root或任何符号链接时标记`BLOCKED`，不删除文件。
- 支持`--dry-run`只读预览；实际执行后保留数据库Session、Job、来源Manifest、Release和SHA。
- 每次成功、阻断或失败均写入`governance.audit_log`，记录注册字节、Artifact角色/SHA、实际发现字节和结果。

## 2. 开发库在线验证

### 2.1 Migration与数据安全

| 项目 | 结果 |
|---|---:|
| 数据库 | `TMS_G0_DEV` |
| Migration | `sql2014_0012 -> sql2014_0013` PASS |
| Schema验证 | 25 schemas、55 tables、4 views，PASS |
| `test.test_run` | 77 → 77 |
| `test.unit_result` | 92,335 → 92,335 |
| `test.measurement` | 1,588,741 → 1,588,741 |

### 2.2 合成过期Artifact清理

使用独立合成Session、Job和三个小文件验证，不修改仍在7天保留期内的真实520 PAT结果。

| 项目 | 结果 |
|---|---:|
| dry-run发现文件 | 3 |
| 实际删除字节 | 43 bytes |
| Session清理状态 | `CLEANED` |
| Artifact物理状态 | `DELETED` |
| stale `CLEANING`恢复 | PASS（模拟中断1小时后重新Claim） |
| 治理审计 | 1 row，内容可解析且结果为`CLEANED` |
| 合成记录收尾 | `integration_cleanup=PASS` |

合成文件、Artifact、Job、Session和验收审计在核验后按外键顺序清除，开发库未遗留测试垃圾。

### 2.3 容量在线验证

- 使用真实520文件字节规模计算预留：1,587,651,687 bytes。
- 工作盘验证时剩余约263.72 GiB，高于预留加10 GiB安全余量。
- `sp_getapplock`容量锁、全局/用户用量查询、Session `reserved_bytes`写入与读取全部通过。
- 容量合成Session验证后删除，没有创建Worker Job或重复执行520 PAT。

## 3. 自动化验证

| 验证 | 结果 |
|---|---|
| 本次变更的19个Python文件 Ruff | PASS |
| 后端 Unit/API/Worker/Migration | 109 passed，4条既有openpyxl警告 |
| 前端测试 | 16 passed（6 files） |
| TypeScript + Vite生产构建 | PASS，1分20秒；既有大Chunk警告仍存在 |
| 物理清理文件边界测试 | 删除精确目录、保留同级文件、逃逸阻断、缺失识别全部PASS |
| SQL Server在线Schema验证 | PASS |
| 合成过期Artifact SQL E2E | PASS |
| SQL容量锁与预留写入 | PASS |

## 4. 做得好的部分

- 物理文件删除与数据库证据分离：释放磁盘但保留业务审计链。
- 先验证全部Artifact路径，再执行目录删除，避免部分校验后误删。
- 清理只处理终态Job，并通过Session状态Claim避免两个清理进程重复操作。
- 配额不是简单限制源文件大小，而是同时考虑活跃任务预留和未清理结果实际大小。
- 在线验收使用小型合成文件，并验证正式事实零增长，避免为测试破坏真实PAT结果。

## 5. 不确定性与限制

- 当前提供幂等命令和dry-run，尚未注册Windows Task Scheduler或Windows服务定时执行。
- 全仓Ruff扫描仍有97个既有历史规则问题，主要位于未改动的旧API和测试文件；本里程碑采用“本次变更文件必须全绿”的门禁，没有借机机械改写旧代码。
- 50%预留比例来自已验收杰群520任务的约1.10 GiB峰值并保留余量；开放其他厂家或算法前必须重新测量峰值并调整。
- `BLOCKED`状态不会自动重试，需管理员修正路径或符号链接问题后人工处置。
- 文件系统删除和SQL状态更新无法组成单一事务；删除成功但SQL暂时失败时，`CLEANING`超过30分钟可被重新Claim，下次运行会识别目录`MISSING`并收敛为`CLEANED`。
- 生产环境尚未执行`sql2014_0013`，也尚未批准生产配额和清理调度频率。

## 6. 下一步

1. 将API、Worker和Cleanup注册为Windows服务/计划任务，验证重启、断网恢复、日志轮转和每日dry-run告警。
2. 生产发布前审批数据库备份、Migration窗口、工作盘容量、服务账号权限和清理频率。
3. 进入Q1 Parquet/Arrow临时交互Workspace，沿用本次容量预留与TTL清理合同。
