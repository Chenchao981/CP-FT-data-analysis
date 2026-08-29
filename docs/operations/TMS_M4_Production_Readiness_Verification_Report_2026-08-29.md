# TMS v1.0 Core M4 生产就绪验证报告

- 验证日期：2026-08-29
- 验证环境：Windows 11、PowerShell 5.1、项目 `.conda-env`
- 范围：生产配置、受管目录/ACL 预检、API/Worker 探针、计划任务、日志轮转与脱敏、SQL 备份/恢复/Migration、可复现发布包

## 1. 结论

M4 源码、DryRun 安全合同、PowerShell AST、发布包检查、解包 launcher smoke 与本机随机空库 Migration 已通过。后端全量回归为 `393 passed, 1 skipped, 0 failed`。

本结论不等于生产上线批准。生产 SQL 备份/恢复演练、目标服务账号 ACL、目标 Windows Server 计划任务注册与 secret bootstrap 仍是外部验收门。

## 2. 自动化验证结果

| 项目 | 结果 | 证据 |
|---|---:|---|
| M4 专项、runtime 安全、日志、Formal Cleanup 合同 | PASS | `38 passed in 11.98s` |
| 后端全量 pytest | PASS | `393 passed, 1 skipped, 4 warnings in 21.30s` |
| PowerShell AST | PASS | 14 个 M4/runtime/config 脚本全部无语法错误 |
| M4 Python Ruff `F,I` | PASS | 0 error |
| 全仓 Ruff `F,I` | 非阻断存量问题 | 25 个非 M4 文件 `I001` import-order，无 `F` 错误；本子包未越界机械修改 |
| 计划任务 `-ValidateOnly` | PASS | API、Worker、QuickCleanup、FormalCleanup 共 4 个任务均 `VALID` |
| Formal Cleanup 独立合同 | PASS | 03:00、默认 DryRun；仅 `-FormalCleanupMode Delete` 才传 `--delete` |
| Formal orphan root 扫描 | PASS | 纯数字直接 Job root；终态/保留期/lease/permanent artifact 失败关闭；DryRun/Delete/拒绝均审计 |
| 备份/恢复/Migration DryRun | PASS | 不连库；白名单、绝对路径、禁止覆盖、checksum/verifyonly 合同通过 |
| 发布可复现性 | PASS | 相同源码/版本两次 ZIP 字节完全一致 |
| ZIP archive inspection | PASS | 路径、排序、固定时间戳、CRC、manifest size/SHA256、secret scan、禁止文件均通过 |
| 解包 launcher `-ValidateOnly` | PASS | `RELEASE_VALID` |

pytest 的 1 个 skip 是当前环境缺少 PowerShell Core (`pwsh.exe`) 时的兼容性测试；项目正式运行合同为 Windows PowerShell 5.1，相关 AST 与执行测试已通过。四条 warning 均来自 openpyxl 对 `datetime.utcnow()` 的上游弃用提示，不影响本次结果。

## 3. 真实空库 Migration 验证

本机 SQL 验证使用当前 PowerShell 进程已有的 runtime，未读取、显示或传递连接串/密码到命令行。

1. 随机库名严格匹配 `NCE_TMS_M4_<32HEX>_MIGRATION_TEST`。
2. 创建前用参数化 `DB_ID` 确认不存在。
3. Alembic 从空库连续执行 `sql2014_0001` 至唯一 head `sql2014_0018`。
4. 核对 `alembic_version`、用户表数量和 Dataset/Processing Run Current 一致性。
5. `finally` 仅对同一个经过正则验证的随机库执行 `SINGLE_USER ... ROLLBACK IMMEDIATE` 和精确 `DROP DATABASE`。
6. 最终输出：`EMPTY_MIGRATION:PASS`、`EMPTY_MIGRATION_CLEANUP:VERIFIED_ABSENT`。

## 4. 安全合同验证

- Production 模式在 PowerShell wrapper 和 Python Settings 两层均要求：强 JWT、SQL repository、Integrated Security DB URL、精确 Database/Server/Schema、受管 Source/Upload/Work/Quick/Log 根。
- Schema revision 必须等于发布包中动态识别的唯一 Alembic head；占位符、弱值、密码 DB URL、库/服务器不一致均失败关闭。
- Runtime 配置禁止 JWT/Health Token 字面量和 DB password；服务账号密码仅使用 `PSCredential` 交给 Task Scheduler。
- ACL preflight 不创建目录、不调用 `Set-Acl`；要求非管理员服务身份对 Source 只读，对 Upload/Work/Quick/Log 具有 Modify。
- API ready、Worker ready file 与已认证 Worker registry 必须对 Worker ID、Database、Server、Schema 达成一致，心跳过期或进程不存在均失败。
- 日志文件名经过白名单字符清洗，按字节数+保留天数轮转；只删除当前进程的过期数字轮转文件，对 Token/JWT/PWD/Password/带密码 URL 脱敏。
- Formal orphan root 扫描只处理 `TMS_WORK_ROOT` 下规范的纯数字直接子目录；不是正式 Job、非终态、保留期内、活跃 lease、存在永久 artifact 或存在尚活跃的已登记临时 artifact 时全部保留。删除前在 SQL Server 中加行锁复核，文件树逐层拒绝 reparse/越根/非常规入口，超限目录只记录 `BLOCKED` 审计且不删除。

## 5. 未在本机执行的外部验收

| 外部验收项 | 负责角色 | 通过证据 |
|---|---|---|
| 生产 DB pre-check、COPY_ONLY/CHECKSUM 备份、VERIFYONLY | DBA | 脚本 Execute 输出、备份字节数、SchemaRevision、变更单 |
| 独立 SQL 测试实例 restore drill | DBA | 白名单测试库、VERIFYONLY、restore 耗时、schema/current PASS |
| 真实服务账号 ACL | Windows 管理员 | 非管理员服务会话的 preflight 全部 VALID |
| 目标机计划任务注册/重启/重试/卸载 | Windows 管理员 | 4 任务 definition VALID、重启后 ProbeRuntime VALID、卸载 VERIFIED_ABSENT |
| Windows secret bootstrap 注入与轮换 | 安全/运维 | 任务参数无秘密、日志 secret scan 无泄漏、旋转演练记录 |

## 6. 已知非阻断项

1. 全仓还有 25 个存量 `I001` import-order 格式问题；不影响运行和本次 M4 产物，建议在独立格式化变更中统一处理。
2. Python `requirements.txt` 仍使用版本范围，ZIP 产物本身可复现，但重新安装第三方依赖时的每个 wheel 尚未通过平台 lockfile 锁定。
3. 本 M4 子包按范围未改动前端；包含前端源码与 `package-lock.json`，未把生产静态站点托管配置视为已验收。
