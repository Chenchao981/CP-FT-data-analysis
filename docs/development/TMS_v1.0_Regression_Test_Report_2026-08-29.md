# TMS v1.0 Core 回归测试报告

- 报告日期：2026-08-29
- 被测版本：TMS v1.0 Core 候选、Alembic `sql2014_0018`
- 开发数据库：`TMS_G0_DEV`，SQL Server 2014 SP2 Enterprise 12.0.5000.0
- 最终提交：`FINAL_VERIFICATION_PENDING`
- 最终发布包 SHA-256：`FINAL_VERIFICATION_PENDING`
- 最终测试结论：`FINAL_VERIFICATION_PENDING`

## 1. 结论

后端、前端、SQL Server Migration、Worker、真实 CP/FT、Quick PAT、A5 Lifecycle、RBAC、运维脚本和发布包均已建立自动化或真实开发库验证。已取得的专项证据没有发现 Canonical 行数漂移、错误 Current、未知良率补零、跨 Owner 放行或越出受管根删除。

最后一批一致性修复合并后必须重新执行本报告第 6 节的完整命令矩阵。当前文档有意不沿用修改前的测试总数作为最终结论；最终总数、耗时、提交号和发布包 SHA 均以 `FINAL_VERIFICATION_PENDING` 标记，未替换前不得签发仓库交付 PASS。

## 2. 做了什么

### 2.1 自动化测试范围

- 后端：API 合同、认证/RBAC/Owner、Source Catalog、CP/FT Writer、Lot-Spec、Job Queue、staged/finalize、Dataset Current、管理 KPI、crosswalk、Worker 运维、A5 Export/Archive/Reprocess、正式/Quick Artifact 清理、日志和 Windows 运行配置。
- 前端：统一认证请求层、四个固定入口、服务端分页/筛选、Dataset Current 目录、Job 详情、URL 深链、管理质量视图、crosswalk、Operations/Worker 状态和 A5 动作反馈。
- Migration：`sql2014_0001 -> 0018` 静态链、现有 `TMS_G0_DEV` 增量升级、随机空库全链升级、Current/Schema 一致性。
- PowerShell/发布：生产配置失败关闭、四个计划任务、ACL 预检、备份/恢复 DryRun、日志脱敏、可复现 ZIP、清单/CRC/秘密/禁止文件检查和解包 launcher smoke。

### 2.2 真实 SQL 与数据回归

- 执行华虹 CP、日月新 FT、Jetech CP 三组真实受控副本，核对全部输入 SHA、Manifest、Run/Unit/Measurement、Lot、参数、良率和 Current。
- 对原子 finalize 的 7 个提交边界注入异常，确认同一事务回滚；恢复 staged intent 时不重新运行 Cleaner。
- 对 A5 最新导出固定产生一个成功 Job；逻辑归档和显式重清洗在真实 SQL 外层事务中验证并回滚。
- 对 Quick PAT 执行 520 文件、约 2.83 GiB、681.38 万 record 计算，并确认正式 Canonical 行数不增长。

## 3. 确定的事实

### 3.1 当前数据库基线

| 指标 | 回归前/回归后 |
|---|---:|
| `test.test_run` | 139 |
| `test.unit_result` | 291,127 |
| `test.measurement` | 5,578,114 |
| Published Current Dataset Version | 10 |
| Alembic Revision | `sql2014_0018` |

`NEEDS_INPUT` 的历史 Job 是保留的待补录业务状态，不等同于 Worker 正在执行；运行健康判断必须同时使用状态、租约、Worker registry 和一致性接口，不能仅数 Job 行。

### 3.2 真实样本回归

| 场景 | 核心断言 | 结果 |
|---|---|---|
| 华虹 CP Dataset 43 | 25 Run、3,875 Unit、13 Item、50,375 Measurement、97.419355% | PASS |
| 日月新 FT Dataset 44 | 6 Run、35,350 Unit、18 Item、636,300 Measurement；Yield NULL | PASS |
| Jetech CP Dataset 46 | 1 Run、2,581 Unit、22 Item、56,782 Measurement、92.716002% | PASS |
| 全来源 Writer 血缘 | 1/1、6/6、1/1 均 `WRITER_VERIFIED` | PASS |
| Finalize Manifest | Job 95/96/98 均 `FINALIZED`，attempt 1 | PASS |
| Quick PAT | 520 文件、3,041,085,645 bytes、6,813,800 records、23 参数、91.894 s | PASS |

### 3.3 A5 回归

| 场景 | 核心断言 | 结果 |
|---|---|---|
| Export Job 148 | Dataset 46、Release 11、Parent 98、3 Artifact、`SUCCESS/READY` | PASS |
| Export 非变异 | `139 / 291,127 / 5,578,114 / Current 10` 不变 | PASS |
| Archive rollback | Current View `(1,1,2581,56782) -> 0`，Facts 不变，Operations `HEALTHY`，回滚恢复 | PASS |
| Reprocess rollback | Parent/Batch/Release/Profile 血缘正确，幂等不重复建 Job，排队不改 Facts，回滚恢复 | PASS |
| 正式 Artifact Cleanup | DryRun 默认、精确 Job root、TTL/租约/永久 Artifact/reparse point 失败关闭、审计保留 | 自动化 PASS；目标机计划任务未执行 |

### 3.4 Migration 与发布验证

1. `TMS_G0_DEV` 已从 0015 顺序升级到 0018；升级前后正式事实计数不变。
2. 随机临时库严格使用 `NCE_TMS_M4_<32HEX>_MIGRATION_TEST` 形式，从空库执行 0001～0018。
3. 空库验证输出 `EMPTY_MIGRATION:PASS` 和 `EMPTY_MIGRATION_CLEANUP:VERIFIED_ABSENT`；只对经过正则和不存在预检的精确随机库做删除。
4. 发布构建器已证明相同源码/版本两次 ZIP 字节可复现、manifest size/SHA、CRC、路径和禁止文件检查通过；最终交付 ZIP 的 SHA 仍待回填。
5. 生产数据库备份/恢复没有在本机冒充执行；当前只完成脚本 DryRun 合同和本机随机空库 Migration。目标环境 restore drill 是外部门。

## 4. 不确定的和未执行的事项

1. 最终全量数字：`FINAL_VERIFICATION_PENDING`。最近一次中间 M4 记录是后端 `369 passed, 1 skipped`，但其后仍有一致性修复，因此不能作为最终签字数字。
2. 最终前端测试数、生产 build 产物和浏览器端 A5 复跑：`FINAL_VERIFICATION_PENDING`。
3. 最终发布提交、ZIP 文件名、字节数和 SHA-256：`FINAL_VERIFICATION_PENDING`。
4. SQL Server 2014 SP3、目标服务账号 ACL、目标机计划任务、HTTPS、正式备份/恢复和连续运行未执行。
5. SAP-B1/MES/QMS 没有生产接口；crosswalk PENDING 不代表企业主数据已经批准。

## 5. 验证证据

### 5.1 自动化结果回填表

| 测试组 | 最终结果 | 最终耗时/备注 |
|---|---|---|
| 后端全量 pytest | `FINAL_VERIFICATION_PENDING` | 应为 0 failed；skip/warning 必须说明 |
| 后端 Ruff `F,I` | `FINAL_VERIFICATION_PENDING` | 新增/修改文件必须 0 error |
| 前端 Vitest | `FINAL_VERIFICATION_PENDING` | 应为 0 failed |
| 前端 TypeScript + Vite build | `FINAL_VERIFICATION_PENDING` | 应为 PASS |
| PowerShell AST / ValidateOnly | `FINAL_VERIFICATION_PENDING` | API、Worker、QuickCleanup、FormalCleanup |
| 发布 ZIP 双构建/inspection/smoke | `FINAL_VERIFICATION_PENDING` | 回填 SHA-256 |
| 认证浏览器 UAT | `FINAL_VERIFICATION_PENDING` | 回填角色、关键页面、console |

### 5.2 已通过的专项入口

- `scripts/g0/verify_sql2014_schema.py`
- `scripts/g0/verify_atomic_finalize_sql_e2e.py`
- `scripts/g0/verify_a5_archive_sql_e2e.py`
- `scripts/g0/verify_a5_reprocess_sql_e2e.py`
- `scripts/g0/verify_quick_pat_e2e.py`
- `scripts/g0/verify_quick_pat_sql_e2e.py`
- `scripts/windows/invoke_tms_empty_database_migration_smoke.ps1`
- `scripts/release/build_tms_release.py`

## 6. 最终回归命令入口

在仓库根目录执行，运行配置只通过环境加载，不把密码或 Token 放入命令行：

```powershell
$env:PYTHONPATH = "$PWD\backend"
& .\.conda-env\python.exe -m pytest -q tests
& .\.conda-env\python.exe -m ruff check backend scripts tests --select F,I

Push-Location .\frontend
npm test
npm run build
Pop-Location

.\scripts\windows\run_tms_api.ps1 -ValidateOnly
.\scripts\windows\run_tms_worker.ps1 -ValidateOnly
.\scripts\windows\run_tms_cleanup.ps1 -ValidateOnly
.\scripts\windows\run_tms_formal_cleanup.ps1 -ValidateOnly
.\scripts\windows\install_tms_scheduled_tasks.ps1 -ValidateOnly
```

真实 SQL E2E 必须先通过 `TMS_G0_DEV / sql2014_0018 / SQL Server` 身份保护；破坏性测试只能使用显式白名单随机测试库或外层回滚事务。

## 7. 下一步

1. 主线完成最后 P1 后运行第 6 节全量矩阵，回填最终数字、耗时、warning 解释、提交号和发布包 SHA。
2. 执行一次最终认证浏览器 UAT，覆盖管理员、管理/质量、CP/FT 工程师、深链、A5 和 Unauthorized。
3. 最终敏感信息/大文件/暂存清单检查通过后才提交和推送；不纳入原始数据、Artifact、日志、缓存、账号、`.env.runtime.ps1` 或 `.remember/`。
4. 在 G3 目标环境另行形成 UAT、备份恢复和运行观察报告；本报告不签发生产上线。
