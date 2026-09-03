# TMS v1.0 Core 回归测试报告

- 报告日期：2026-08-29
- 被测版本：TMS v1.0 Core 候选、Alembic `sql2014_0018`
- 开发数据库：`TMS_G0_DEV`，SQL Server 2014 SP2 Enterprise 12.0.5000.0
- 验证基线提交：`0dca74a`（报告回填为后续 docs-only 提交）
- 最终发布包 SHA-256：`D84E7BCC1CDADDAE19C6ADEFD694EB32AD605FAA6C79CF2BDE7E500A54D9D9DC`
- 最终测试结论：**仓库与开发库 G0-G2 PASS；G3/G4 未执行**

## 1. 结论

后端、前端、SQL Server Migration、Worker、真实 CP/FT、Quick PAT、A5 Lifecycle、RBAC、运维脚本和发布包均已建立自动化或真实开发库验证。已取得的专项证据没有发现 Canonical 行数漂移、错误 Current、未知良率补零、跨 Owner 放行或越出受管根删除。

最后一批一致性修复合并后，主线已重新执行第 6 节的完整命令矩阵、真实 SQL A5/Schema 复核和认证浏览器 UAT。最终结果没有失败，Canonical 与 Current 事实未漂移，因此签发仓库与开发库 G0-G2 PASS；该结论不包含目标服务器或生产上线。

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
| 浏览器 Export Job 157 | Dataset 46、Release 11、3 Artifact、`SUCCESS/READY/PRESENT`，下载按钮成功触发 | PASS |
| Export 非变异 | `139 / 291,127 / 5,578,114 / Current 10` 不变 | PASS |
| Archive rollback | Current View `(1,1,2581,56782) -> 0`，Facts 不变，Operations `HEALTHY`，回滚恢复 | PASS |
| Reprocess rollback | Parent/Batch/Release/Profile 血缘正确，幂等不重复建 Job，排队不改 Facts，回滚恢复 | PASS |
| 正式 Artifact Cleanup | DryRun 默认、精确 Job root、TTL/租约/永久 Artifact/reparse point 失败关闭、审计保留 | 自动化 PASS；目标机计划任务未执行 |

### 3.4 Migration 与发布验证

1. `TMS_G0_DEV` 已从 0015 顺序升级到 0018；升级前后正式事实计数不变。
2. 随机临时库严格使用 `NCE_TMS_M4_<32HEX>_MIGRATION_TEST` 形式，从空库执行 0001～0018。
3. 空库验证输出 `EMPTY_MIGRATION:PASS` 和 `EMPTY_MIGRATION_CLEANUP:VERIFIED_ABSENT`；只对经过正则和不存在预检的精确随机库做删除。
4. 发布构建器已证明相同源码/版本两次 ZIP 字节可复现、manifest size/SHA、CRC、路径和禁止文件检查通过；最终 ZIP 为 `NCE-TMS-v1.0-core.zip`，481,749 bytes、220 个 Manifest 文件，SHA-256 为 `D84E7BCC1CDADDAE19C6ADEFD694EB32AD605FAA6C79CF2BDE7E500A54D9D9DC`。
5. 生产数据库备份/恢复没有在本机冒充执行；当前只完成脚本 DryRun 合同和本机随机空库 Migration。目标环境 restore drill 是外部门。

## 4. 不确定的和未执行的事项

1. SQL Server 2014 SP3、目标服务账号 ACL、目标机计划任务、HTTPS、正式备份/恢复和连续运行未执行。
2. SAP-B1/MES/QMS 没有生产接口；crosswalk PENDING 不代表企业主数据已经批准。
3. Vite 生产构建仍提示两个主 chunk 大于 500 kB；质量驾驶舱在 5,578,114 条 Measurement 的首次全窗口读取约 10～13 秒（含浏览器轮询粒度）。两项均应在 G3 建立 SLO、索引/聚合与拆包优化证据。
4. 全仓仍有 25 个本次变更范围外的历史 Ruff `I001`；本次新增/修改 Python 文件 `F/I` 为零，全仓 `F/E9` 为零。

## 5. 验证证据

### 5.1 自动化结果回填表

| 测试组 | 最终结果 | 最终耗时/备注 |
|---|---|---|
| 后端全量 pytest | PASS：`393 passed, 1 skipped, 4 warnings` | 34.82 s；4 warning 均为 openpyxl `utcnow()` 弃用提示 |
| 后端 Ruff | PASS | 全仓 `F/E9` 0；本次新增/修改 Python `F/I` 0；历史范围外 `I001` 25 |
| 前端 Vitest | PASS：25 files / 91 tests | 100.64 s；仅 jsdom 不实现伪元素 `getComputedStyle` 的已知提示 |
| 前端 TypeScript + Vite build | PASS：13,055 modules | 24.03 s；保留大 chunk P2 warning |
| PowerShell AST / ValidateOnly | PASS | 14 个脚本 AST；API、Worker、QuickCleanup、FormalCleanup 4 项 ValidateOnly |
| 发布 ZIP 双构建/inspection/smoke | PASS | 481,749 bytes；220 files；双构建一致；SHA-256 见报告头 |
| 认证浏览器 UAT | PASS | 4 角色；质量/crosswalk/Operations/A5/Unauthorized；console 0 warning/error |

### 5.2 已通过的专项入口

- `scripts/g0/verify_sql2014_schema.py`
- `scripts/g0/verify_atomic_finalize_sql_e2e.py`
- `scripts/g0/verify_a5_archive_sql_e2e.py`
- `scripts/g0/verify_a5_lifecycle_concurrency_sql_e2e.py`
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
npm test -- --run
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

1. 保持发布 ZIP、源码基线和本报告的 SHA/提交关联；docs-only 回填不改变已测试程序内容。
2. 在 G3 目标环境另行形成 UAT、性能 SLO、备份恢复和运行观察报告；本报告不签发生产上线。
3. G3 前优先评估质量驾驶舱聚合/索引与前端 chunk 拆分，再用真实并发和时间窗口验证。
