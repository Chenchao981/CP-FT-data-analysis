# TMS v1.1 前端功能闭环回归测试报告

- 报告日期：2026-08-30
- 被测范围：TMS v1.1 Functional Closure 候选
- 数据库基线：SQL Server 12 / `TMS_G0_DEV` / `sql2014_0018`
- 测试边界：功能、数据口径、只读 SQL、浏览器任务流；不扩建安全项目
- 当前结论：**自动化、构建、真实 SQL 只读和本机浏览器功能验收 PASS；生产认证、G3、G4 未执行**
- 发布包证据：`NCE-TMS-v1.1-functional-rc3.zip`，496,078 bytes，213 files，SHA-256 `a81da325ab3ad7b05edda4f1ff1e464331afc03c597c7315eec793715028d3c2`

## 1. 测试结论

本轮对前端路由、一线正式任务、历史正式数据、比较/明细、Quick PAT、质量看板、后端查询合同、CP Spec 合同和 Windows 本机运行脚本进行了自动化与真实环境回归。

现有证据确认：

- 最终后端全量回归为 457 passed、1 skipped、4 warnings；
- 前端首轮全量出现 1 个等待时序失败；把该测试的默认等待上限从 1 秒放宽到 5 秒后，目标 2/2 与最终全量 24 files、116 tests 均 PASS，业务代码未因此修改；生产构建处理 13,055 modules；
- 真实 SQL 只读验收在 SQL Server 12、`TMS_G0_DEV/sql2014_0018` 上 PASS，共执行 173 条只读语句、0 blocked，前后关键事实计数不变；
- Current Catalog 在同一只读窗口完成 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages、7 filters 的完整复核，服务分页合并后的完整快照与独立事实一致；
- 本机浏览器完成四入口、历史多选、禁跨阶段、FT 比较、CP 明细、参数深链、Quick 清单、Job 追溯和质量看板验收，控制台无 warning/error；
- Cleaner Release 血缘 SQL 缺陷以及最终审计发现的三个 P1 均已修复：图表强制 Current + `PUBLISHED`；Catalog Lot 使用 Canonical `test_run`；`TMS_CURRENT_DATA` grant-only 读取收回到 Current + `PUBLISHED`，Owner/Admin 历史读取保留且 WRITE 未放宽。

当前真实样本的 10 个 Current Dataset 中，9 个为单 Lot、1 个无 Lot、0 个多 Lot。多 Lot 只有合同与自动化分支覆盖；上游 CP 多 Lot 逐 Lot Spec Golden 和真实 E2E 仍未完成。

## 2. 测试范围

### 2.1 后端

- CP Wafer 与管理质量良率：PASS、FAIL、UNKNOWN、ABORT、空已知分母。
- CP CSV Triplet Writer：单 Lot、同 Spec 多 Lot逐 Lot证据、规范化参数/单位/条件/上下限、冲突与缺证据失败关闭。
- Dataset Compare：1～8 个、同阶段、Current、权限范围、CP Spec 兼容、FT 无 Spec 场景。
- Dataset Detail/Chart：Lot、Wafer、Bin、参数、分页上限、超末页、NULL 字段、SQL Server 2014 OFFSET/FETCH，以及 Current + `PUBLISHED` 一致门禁。
- Current Catalog：Canonical Lot、Product/Lot/Wafer/Batch/Cleaner/Owner/厂家/阶段/时间筛选、分页、有效 Product、Owner 归档能力和 Cleaner Release 血缘。
- Quick PAT：Manifest 预览/确认、来源变化拒绝、服务端分页、状态/日期筛选、UTC 规范化。
- 管理质量：正确良率、UNKNOWN/ABORT、上海业务日、失败 Job 范围、确定性最新 Current。
- Windows 运行：UTF-8 JSON 状态文件、PowerShell 5.1/7 兼容和精确工作区识别。
- 功能授权边界：grant-only 仅 Current + `PUBLISHED`，Owner/Admin 历史读取保留，WRITE 不放宽；list/gate/summary/compare/chart/details/G0 调用语义一致。

### 2.2 前端

- 根路由、工程/量产父路由、四个 CP/FT 叶子入口和合法角色默认页。
- 正式提交自动打开 Job、`REPROCESS_BATCH` 动作、URL 状态恢复和上海时间输入。
- 统一 API 错误：状态码、错误码、字段错误、重试与建议动作。
- 历史正式数据：业务筛选、单/多选、最多 8 个、跨 CP/FT 禁止、Product 补录、Job/分析下钻。
- 正式分析：FT 多 Dataset 比较、CP Unit 明细、Lot/Wafer/Bin/参数筛选和参数 URL 深链。
- Quick：Manifest 确认弹窗、会话分页/筛选、下载错误。
- 质量看板：核心 KPI、趋势、方法说明、上海日期、失败 Job 不适用状态。
- 旧页面/API 清理后的路由、组件测试与生产构建。

### 2.3 真实环境

- SQL 身份和 Schema 失败关闭；只允许 SELECT 或只读 CTE。
- CP 选择单 Dataset 执行比较、详情分页、结果对账和质量摘要。
- FT 选择双 Dataset 执行比较、详情分页、结果对账和质量摘要。
- Canonical 与 Current 关键计数在验收前后完全一致。
- 本机 API/Worker/前端实际运行，使用浏览器完成业务任务流，而不是只验证静态页面。

## 3. 自动化结果

| 测试组 | 结果 | 证据解释 |
|---|---:|---|
| 后端首轮全量 pytest | **PASS：435 passed / 1 skipped / 4 warnings** | 无失败；为浏览器缺陷修复前首轮结果 |
| 最终修复后后端全量 | **PASS：457 passed / 1 skipped / 4 warnings** | Cleaner Release 血缘及三个最终 P1 修复均已进入最终基线 |
| 功能授权边界目标回归 | **PASS：49 tests** | grant-only、Owner/Admin、WRITE 非放宽及 list/gate/summary/compare/chart/details/G0 一致性 |
| 前端首轮全量 Vitest | **FAIL：1 个等待时序用例** | 默认 1 秒等待在本轮全量负载下不足；业务断言未被删减 |
| 前端等待时序目标回归 | **PASS：2 / 2 tests** | 测试等待上限从 1 秒放宽到 5 秒，业务代码未修改 |
| 前端最终全量 Vitest | **PASS：24 files / 116 tests** | 路由、API、组件和任务流覆盖 |
| 前端 TypeScript/Vite build | **PASS：13,055 modules** | 生产构建完成 |
| Current Catalog 真实只读复核 | **PASS：10 keys / 9 Canonical Lot members / 7 distinct Lot / 5 service pages / 7 filters** | 完整快照一致；样本为 9 单 Lot、1 无 Lot、0 多 Lot |
| Release ZIP 双构建/inspection/smoke | **PASS：213 files / 496,078 bytes** | `NCE-TMS-v1.1-functional-rc3.zip` 双构建 SHA-256 一致；CRC、Manifest、秘密/禁止文件扫描和解包 launcher smoke 为 VALID |

4 个 warning 均来自 openpyxl 对 `datetime.utcnow()` 的弃用提示，已在测试输出中留证；它们不是本轮功能断言失败，也没有被静默删除。

## 4. 真实 SQL 只读验收

### 4.1 保护条件

验收入口 `scripts/g0/verify_v11_functional_sql_readonly.py` 在查询前验证：

- 数据库名必须精确为 `TMS_G0_DEV`；
- Alembic Revision 必须精确为 `sql2014_0018`；
- 数据库引擎必须为 Microsoft SQL Server，本次实际 major version 为 12；
- 只允许单条 SELECT 或只读 CTE；INSERT、UPDATE、DELETE、MERGE、DDL、EXEC、SELECT INTO 等在执行前拒绝；
- 输出不包含连接串、服务器、登录名、Dataset/Lot/Wafer/参数/Unit 等业务身份明文。

### 4.2 验收结果

| 断言 | 结果 |
|---|---|
| 数据库与 Schema 身份 | PASS：SQL Server 12 / `TMS_G0_DEV` / `sql2014_0018` |
| 只读审计 | PASS：173 条已执行，0 blocked，未写入 |
| CP 单 Dataset | Compare、Detail、分页、质量摘要与独立计数对账 PASS |
| FT 双 Dataset | Compare、Detail、分页、质量摘要与独立计数对账 PASS |
| Current Catalog | 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages、7 filters；完整快照一致 |
| Current Lot 样本 | 9 单 Lot、1 无 Lot、0 多 Lot |
| 良率 | PASS/(PASS+FAIL)，UNKNOWN/ABORT 分离；零分母为 NULL |
| 分页 | 首/中/末/超末页合同 PASS |
| 数据变异 | Canonical 与 Current 前后计数不变 |
| 总结 | **PASS** |

本测试可以证明查询结果、分页和口径与当前开发库事实相互对账；它不能证明生产环境并发、写入事务、备份恢复或正式业务权限。

## 5. 浏览器功能验收

| 编号 | 业务任务 | 实际断言 | 结果 |
|---|---|---|---|
| B01 | 固定入口 | 工程 CP、工程 FT、量产 CP、量产 FT 均可进入 | PASS |
| B02 | 历史正式数据 | 以业务字段检索，无需手填 Dataset ID | PASS |
| B03 | 多选规则 | 同阶段可多选，最多 8 个；CP/FT 跨阶段选择被禁用 | PASS |
| B04 | FT 比较 | 真实双 Dataset 比较结果与服务端数据可见 | PASS |
| B05 | CP 明细 | 真实 Unit 明细、分页和结果字段可见 | PASS |
| B06 | 参数深链 | 参数筛选进入 URL，刷新后恢复 | PASS |
| B07 | Quick 清单 | 先显示 Manifest 文件范围，再确认创建 PAT | PASS |
| B08 | Job 追溯 | 状态、父子 Job、Cleaner Release、来源与 Dataset 下钻可用 | PASS |
| B09 | 质量看板 | 核心 KPI、趋势、筛选、方法说明和最近 Dataset 可用 | PASS |
| B10 | 控制台 | warning 0 / error 0 | PASS |

### 5.1 真实联测与最终审计关闭的缺陷

Current Catalog 查询曾使用不存在的 `processing_run.cleaner_release_id`。真实血缘应为：

```text
processing_run.job_id
  -> processing_job.job_id
  -> processing_job.cleaner_release_id
  -> cleaner_release.cleaner_release_id
```

该问题说明自动化模拟结果不能替代真实 SQL/HTTP/浏览器联测。修复后重启实际服务，Catalog 返回 HTTP 200 和 10 条数据，浏览器任务流继续通过。

最终代码审计还关闭了三个 P1：

1. Chart 查询此前没有与 Compare/Detail 使用同一版本门禁。现已强制 Dataset Version 同时满足 Current 和 `PUBLISHED`，否则返回 `ANALYSIS_VERSION_NOT_CURRENT`。
2. Current Catalog 的 Lot 身份改为从 Canonical `dataset_version_run -> test_run` 派生，Lot 筛选也在 Canonical 成员上执行，不再依赖 summary 行。
3. `TMS_CURRENT_DATA` 曾被扩成全版本读取。现已把 grant-only 调用收回为只读 Current + `PUBLISHED`，Owner/Admin 历史读取保留，WRITE 未放宽；list、gate、summary、compare、chart、details 与 G0 调用统一。Gate/Summary 核心完成二次复核，没有保留额外读锁。

三个 P1 相关目标回归共 49 项 PASS，并进入最终后端全量 457/1/4。在当前同一只读窗口，最终实现的 Catalog 复核结果为 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages、7 filters，完整快照一致；这里不把未在该窗口单独复现的历史中间状态写成真实库事实。

## 6. 回归风险与边界

### 6.1 已关闭风险

- UNKNOWN/ABORT 被误算为 FAIL 或进入良率分母；
- 合法 Dataset 查看角色被分析页面错误拒绝；
- 重新处理动作因前后端动作码不一致被禁用；
- 提交成功后必须人工重新查找 Job；
- 历史分析依赖手填内部 Dataset ID；
- 混合 CP/FT 得到看似有效的比较结果；
- Quick 预览后来源变化仍按旧清单执行；
- PowerShell 5.1/7 因中文路径编码无法正常停止；
- Current Catalog 使用不存在的 Cleaner Release 血缘字段；
- Chart 绕过 Current + `PUBLISHED` 版本门禁；
- Current Catalog Lot 使用 summary 而不是 Canonical `test_run`；
- `TMS_CURRENT_DATA` grant-only 读取被扩大到历史版本，导致功能授权口径漂移。

### 6.2 仍开放的风险

1. 外部 CP Cleaner 尚未提供多 Lot 逐 Lot Spec Golden；本次真实样本为 9 单 Lot、1 无 Lot、0 多 Lot，TMS 只证明了多 Lot 严格接收/拒绝合同和自动化分支，没有真实端到端正向业务证据。
2. 浏览器 UAT 是本机功能模式，不是生产认证/RBAC 验收；安全需求需另行规划。
3. G3/G4、目标服务器连续运行、并发性能、生产备份恢复和正式业务签字均未执行。
4. 发布 ZIP 已完成本机归档与 launcher smoke；这不是目标服务器安装或生产部署证据。
5. SQL Server READ COMMITTED 下仍存在可接受的换版瞬间 P2：分开的只读语句可能分别观察到切换前后状态。Gate/Summary 核心已二次复核，本轮没有为消除该极短窗口增加额外读锁。

## 7. 可复现验证入口

运行配置应从受控环境加载，不把密码或 Token 放入命令行：

```powershell
$env:PYTHONPATH = "$PWD\backend"
& .\.conda-env\python.exe -m pytest -q tests

Push-Location .\frontend
npm test -- --run
npm run build
Pop-Location

& .\.conda-env\python.exe .\scripts\g0\verify_v11_functional_sql_readonly.py
```

最后一个命令要求进程环境已经安全加载 `TMS_DATABASE_URL`；脚本会再次核对数据库名、Schema 和 SQL Server 身份。不得把连接密码直接拼到命令行或报告中。

## 8. 最终 Gate 清单

| Gate | 当前状态 | 关闭条件 |
|---|---|---|
| 最终修复后后端全量 | PASS | 457 passed / 1 skipped / 4 warnings |
| 三个最终 P1 目标回归 | PASS | 49 tests；功能授权边界、版本门禁与 Canonical Lot |
| 前端全量与构建 | PASS | 等待时序目标 2/2；最终 24 files / 116 tests；13,055 modules |
| 真实 SQL 只读 | PASS | 173 语句、0 blocked、计数不变、CP/FT 与 Catalog 对账 |
| 浏览器任务型 UAT | PASS | B01～B10，console 无 warning/error |
| Current Catalog 复核 | PASS | 10 keys / 9 Canonical Lot members / 7 distinct Lot / 5 service pages / 7 filters；完整快照一致 |
| Release ZIP | PASS | `NCE-TMS-v1.1-functional-rc3.zip`；213 files；496,078 bytes；双构建/Manifest/launcher/SHA-256 通过 |
| 生产认证/G3/G4 | 未执行 | 独立计划、目标环境和业务签字 |

## 9. 下一步

1. 为外部 CP Cleaner 建立多 Lot 逐 Lot Spec Golden 套件，再决定是否开放多 Lot 正向业务入口。
2. 安全与生产认证另立计划；本报告不作为生产权限、HTTPS、服务账号或安全上线签字。
