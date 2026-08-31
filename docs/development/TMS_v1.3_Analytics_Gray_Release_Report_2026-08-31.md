# TMS v1.3 Analytics 灰度候选报告

- 报告日期：2026-08-31
- 候选版本：`v1.3-analytics-closure-rc1`
- 数据库 head：`sql2014_0023`
- 候选范围：AC1～AC5、V01～V28 的技术实现及本机 G0～G2.5 验收
- 当前状态：**本机候选 PASS；可作为 G3 准备输入，但当前环境不批准进入 G3**
- 生产状态：**G4 NO-GO；未上线**

## 1. 灰度结论

v1.3 已形成分析闭环候选实现：四个固定 CP/FT 入口下的“历史正式数据 → 分析”包含 Overview、Detail、Parameter、Spatial、Quality、Delivery 六组；后端统一权威统计、正式 Spec/Bin 评价、规则治理、Saved Analysis、Analytics Export 和前端工作台均已进入代码与测试。

本机开发库已升级到 `sql2014_0023`，并以真实 Current Dataset 完成核心技术工作负载验证：FT Dataset `105`～`112`，以及 CP Dataset `113` 的 **V2 Current**。四个固定入口、六组分析页面、真实 CP/FT 钻取、Saved Analysis、前端发起的 Export、四角色认证与越权拒绝均已有本机证据；临时认证账号也已禁用并清空角色。

这些证据只证明本机 G0～G2.5 的已测技术边界。开发库仍没有 Owner/Rule 审批或激活记录，Data/Golden Owner 也未签发正式 Expected；Owner Gate、Data Gate 继续关闭。正式 13 场景性能、最终后端/前端全量回归及冻结运行源码的双构建 Release 已全部取得 PASS，因此本机候选可以作为 G3 准备输入；但不能把它写成 G3 准入或生产批准。

当前数据库服务版本为 SQL Server `12.0.5000`，低于本项目 G3 要求的 SQL Server 2014 SP3+。即使本机剩余证据全部通过，也必须更换到满足版本要求的目标 TEST/UAT 环境后，才可申请 G3。

即使后续本机 G0～G2.5 全部通过，也只表示可提交目标 TEST 评审：

- G3 需要 SQL Server 2014 SP3+ TEST、HTTPS、正式服务账号、正式角色、备份恢复、固定数据性能和业务/质量签字；
- G4 需要生产变更窗口、安全/容量/监控、回滚阈值、分批观察和批准；
- 本机开发库或 Loopback 浏览器证据不能替代 G3/G4。

## 2. 候选范围与不包含范围

### 2.1 候选包含

- 工程 CP、工程 FT、量产 CP、量产 FT 四个固定入口；
- Current Catalog 的 1～8 Dataset 选择、统一 Filter/Rule/View State、深链和权限复核；
- Overview、Detail、Parameter、Spatial、Quality、Delivery 六组页面；
- V01～V28 的技术实现、数据能力门控或 Owner 门控；
- `sql2014_0020`～`sql2014_0023`；
- Canonical 写入事务内正式 Spec Evaluation 与 Bin Mapping Evaluation 物化；
- 版本化规则注册/激活门禁、正式 PAT 共享引擎 Adapter；
- Saved Analysis 修订/恢复和 Analytics Export Queue/Worker/Artifact/TTL；
- 本机运行、生产预检、Migration、Worker、状态检查、受控清理与回退文档。

### 2.2 明确不包含或尚未批准

- VDMOS 独立菜单、浏览器解析原始文件、硬编码 Pass Bin、浏览器端统计引擎；
- 未 Profile/Adapter 验收的 `F:\data` 厂家或格式自动入库；
- 未经 Data Owner 签发的完整正式 Golden；
- 未经 Rule Owner、Technical Owner、Quality Validator 共同批准的动态统计规则；
- 目标 TEST/生产数据库部署、正式账号、HTTPS、备份恢复、安全专项、灾备、生产观察；
- 任何形式的 G3/G4 或“已生产上线”声明。

## 3. Gate 状态

| Gate | 环境 | 必须证据 | 当前状态 |
|---|---|---|---|
| G0 | 本机静态/自动化 | 后端/前端全量、TypeScript/Build、Migration、合同、约定范围 lint/diff | **PASS（本机范围）**：后端 1,011 passed、前端 237 tests、Build、compileall、变更文件 Ruff、diff-check 与双 Release 均通过；全仓严格 Ruff 的既有债务另行披露 |
| G1 | `TMS_G0_DEV` | 真实 CP/FT、筛选/钻取/Saved/Export、Spec/Bin、数据不变 | **PASS（本机技术范围）**：FT `105`～`112`、CP `113 V2`、Spec/Bin、Parameter、Saved、Export Lifecycle、正式性能与事实不变已验证；Owner/Data Gate 仍关闭 |
| G2 | 本机浏览器 | 四入口、1/多/8 Dataset、六分组、空态、URL、Saved/Export、Console | **PASS（本机范围）**：四入口、六分组、真实 CP/FT、Saved/Restore/Delete、前端提交并下载 Export 已验证；不代表目标 TEST/UAT |
| G2.5 | 本机认证 | SYSTEM_ADMIN、CP_ENGINEER、FT_ENGINEER、MANAGER_VIEWER 合法/拒绝矩阵 | **PASS（本机范围）**：四角色菜单/直接 URL 拒绝、切换隔离、退出登录和临时账号禁用/角色清空已验证 |
| G3 | 目标 TEST/UAT | SP3+、HTTPS、正式账号、30～50 次性能、备份恢复、业务/质量签字 | **NO-GO / 未执行**：当前 SQL Server `12.0.5000` 低于 SP3+ 要求，且 Owner/Data Gate 未签发 |
| G4 | 生产分批 | 安全、容量、监控、变更、回滚、观察期和正式签字 | **NO-GO / 未执行** |

G0～G2.5 已取得可复现 PASS，且最终源码的 G1/G2/G2.5 复核无 P0/P1 技术缺陷，本报告更新为“本机候选可作为 G3 准备输入”。申请不等于准入：G3 仍需迁移到 SQL Server 2014 SP3+ 目标环境并独立完成环境、数据、账号与业务审批。任何 Owner-gated 或 Data-gated 能力继续按各自批准状态关闭，不能随候选包自动开放。

## 4. 功能开放策略

### 4.1 默认可用能力

在用户拥有 Dataset 读取权限、Dataset 为允许的 Current+PUBLISHED 范围且数据能力完整时，默认可用：

- V01～V06 Overview、趋势、正式 Bin/Pareto、明细和钻取；
- V09 Scatter、V11 Trend；
- V13～V16 基于可信坐标/Mapping/Spec 的空间能力；
- V18、V21 FT 多参数和维度比较；
- V25 Wafer Summary；
- V26 Saved Analysis、V27 Analytics Export、V28 显示控制。

“默认可用”不绕过数据门控。例如没有正式 Bin Mapping 时 Bin/Pareto/Bin Map/Fail Overlay 必须关闭；没有可信 X/Y 时空间图关闭；没有 PASS/FAIL 时 Yield=NULL；没有正式 Spec 时 OOS/Margin/Cpk 等不计算。

### 4.2 Owner-gated 能力

开发库当前真实审批/激活记录为 0，未创建假批准。以下能力技术实现随包交付，但正式执行继续要求精确的已批准、已激活 Rule Code/Version：

- V04 中的低 Cpk/PAT/SBL 等动态风险；
- V07 BoxPlot；
- V08 Histogram/Normal Fit；
- V10 Correlation；
- V12 Cpk/Ppk；
- V17 Zone；
- V19 Formal PAT；
- V20 SYL/SBL；
- V22 SPC I-MR；
- V23 Margin/OOS；
- V24 PASS/FAIL 分布和 Bin 共现。

未批准时后端返回稳定门禁，例如 `ANALYSIS_RULE_NOT_APPROVED`；前端显示“统计口径待业务批准”。不得隐藏按钮后允许直接 URL/API 绕过，也不得临时在 React 中计算。

### 4.3 数据格式开放范围

- CP：HUAHONG、JETECH、LION 的已验收 Profile；
- FT：RIYUEXIN、RIYUEGUANG 的已验收 DC `FT_XLSX_SCATTER_V1`；
- 其他 `F:\data` 源继续走 Profile/Adapter/Golden Gate，不因本次分析包发布自动开放。

## 5. 候选验证摘要

### 5.1 已取得

| 证据 | 结果 |
|---|---|
| 最终后端全量 | 1,011 passed、4 skipped、16 warnings，43.01 s；4 Skip 均为当前 Windows 账号无 symlink 权限的环境条件 |
| 前端串行全量 / Build | 48 files / 237 tests，635.80 s；Production Build 26.19 s PASS |
| Compileall / diff | PASS |
| Spec Evaluation SQL E2E | 六状态、6/6 幂等、回滚清理 PASS |
| Bin Mapping SQL E2E | 三状态、3/3 幂等、回滚清理 PASS |
| v1.1 兼容只读 SQL | 186 statements、0 blocked、前后正式事实摘要一致 |
| Schema | 25 Schemas、67 Tables、4 Views、7 Roles、12 Permissions、8 DQ Rules |
| Formal PAT Adapter | 4/4 技术向量、真实 Quick 摘要 23/23；Owner Gate 未绕过 |
| 最终双 Release / 解包 API smoke | 275 个 Manifest payload 文件（ZIP 276 entries）；A/B 均 798,680 Bytes、SHA-256 `dffd339152e48e66008dcbf2a50b4c8d15f15bc59d20b934481eb61f58940568`；数据库/Schema 身份正确、无残留进程和临时目录 |
| 正式性能 | 13/13 PASS；并发 1/5 各 30 次、0 error、0 blocked、Canonical 不变；大点采样稳定并保留全部 OOS |
| 真实开发库工作负载 | FT Dataset `105`～`112`；CP Dataset `113 V2 Current`，25 Wafer、3,875 Unit、50,375 Measurement |
| G2 浏览器任务 | 四固定入口和六分组；CP/FT Overview、Detail、Parameter、Spatial、Quality、Delivery；Saved 修订/恢复/删除；前端 Export Job 成功下载 |
| G2.5 认证任务 | SYSTEM_ADMIN、CP_ENGINEER、FT_ENGINEER、MANAGER_VIEWER 的菜单/直接 URL 矩阵、身份切换/退出、临时账号清理 PASS |
| Owner/Data Gate | 开发库审批/激活为 0；未制造假批准；正式 Golden Expected 和 Owner 签字未冻结，相关能力继续关闭 |

### 5.2 本机签发结果与外部门禁

| 证据 | 结果 |
|---|---|
| 最终后端 pytest 全量 | **PASS**：1,011 passed、4 skipped、16 warnings / 43.01 s |
| 前端串行全量 / TypeScript / Build | **PASS**：48 files、237 tests / 635.80 s；Build 26.19 s |
| Ruff / compileall / diff | **PASS（约定范围）**：变更 Python `E4/E7/E9/F` 与 87 个新增文件 format、compileall、diff-check 通过；全仓严格 Ruff 既有债务和 112 个历史未格式化文件未伪装为 PASS |
| v1.3 SQL 专项最终复核 | **PASS**：Parameter、Spec/Bin、Export Lifecycle、v1.1/v1.3 只读与 Canonical 不变量通过 |
| 正式性能：并发 1/5、13 场景 | **PASS**：warmup 2、各 30 次；13/13、0 error、0 blocked、采样稳定；single-flight 边界见回归性能报告 |
| G2 浏览器最终代码复核 | **PASS**：CP 113/V2 与 FT 112/V1 新页面数值和操作符合合同；Console 0 error，仅导航时 ECharts disposed warning |
| G2.5 四角色矩阵 | **PASS（本机范围）**：合法/拒绝路径、身份切换、退出和临时账号清理完成 |
| 最终 Release Build A/B | **PASS**：`v1.3-analytics-closure-rc1`；双包同大小/同 SHA；CRC、Manifest、秘密/禁止路径扫描和真实解包 API ready 通过 |
| 临时数据、账号、Artifact 和服务精确清理 | **PASS**：受控 E2E 夹具清理、4 个临时账号禁用并清空角色、release smoke 进程/临时目录为 0、本机服务已停止；Job 5 按正式 TTL 审计合同保留 |
| Data/Rule Owner | **未签发**：正式 Golden、Expected、逐规则 Approval/Activation 仍为 G3 外部门禁 |
| G3/G4 | **NO-GO / 未执行**：目标 SP3+ TEST、HTTPS、正式账号、备份恢复、业务/质量签字和生产流程未完成 |

## 6. 本机候选运行顺序

下列命令不含凭据；运行配置由操作者在目标机器的受控环境中提供。

```powershell
# 1. 预检与自动化
.\.conda-env\python.exe -m pytest tests\unit -q
Set-Location frontend
npx vitest run --maxWorkers=1
npm run build
Set-Location ..

# 2. 数据库身份、Schema 与专项
. .\.env.runtime.ps1
.\.conda-env\python.exe scripts\g0\verify_sql2014_schema.py
.\.conda-env\python.exe scripts\g0\verify_v13_parameter_analysis.py
.\.conda-env\python.exe scripts\g0\verify_spec_evaluation_materialization_sql_e2e.py
.\.conda-env\python.exe scripts\g0\verify_bin_mapping_materialization_sql_e2e.py
.\.conda-env\python.exe scripts\g0\verify_analytics_export_lifecycle_sql_e2e.py

# 3. 本机前端任务
.\scripts\windows\start_tms_local_test.ps1 -NoBrowser
.\scripts\windows\get_tms_local_test_status.ps1

# 4. Export Worker 按需处理一项
.\.conda-env\python.exe scripts\run_analytics_export_worker.py --once

# 5. 结束并复核无残留
.\scripts\windows\stop_tms_local_test.ps1
.\scripts\windows\get_tms_local_test_status.ps1
```

数据库专项必须验证 `TMS_G0_DEV/sql2014_0023`；任何身份、Revision 或 Server 类型不符都应中止。当前 SQL Server `12.0.5000` 只能用于本机 G0～G2.5 技术验收，不能用于要求 SP3+ 的 G3。运行时不得把 `.env.runtime.ps1`、连接串、密码或 Token 写入报告、提交或发布包。

## 7. G3 建议灰度方案

本报告现已更新为本机 PASS；以下 G3 工作仍未执行，必须在独立目标环境另行审批：

1. DBA 在 SQL Server 2014 SP3+ 独立 TEST 完成升级前备份、恢复演练和 `sql2014_0001 -> 0023` 空库链验证；禁止沿用当前 `12.0.5000` 作为 G3 环境。
2. 只选一个业务小组、一个 Stage、有限厂家/产品/Lot；先只读目录和分析，再开放受控 Saved/Export。
3. 使用正式 SYSTEM_ADMIN、CP 工程、FT 工程、管理只读账号执行完整 URL、直接 API、缓存隔离与审计矩阵。
4. Data Owner 冻结逐文件 SHA、Cleaner Release、Dataset Version、Canonical 数量、Spec/Bin/Rule Version 和 Expected；Quality Validator 签字。
5. Owner-gated 规则逐条批准和激活，不批量插入默认规则；先一条规则、一组 Golden、一项 Feature Flag。
6. 固定 8-Dataset 和 CP Multi-Wafer 工作负载，分别并发 1/5 执行 30～50 次，记录 p50/p95、错误率、响应大小、SQL 数、逻辑读和执行计划。
7. 验证 Export 目录 ACL、SHA、TTL、过期下载、精确 Cleanup；验证备份恢复时间与回退路径。
8. 连续观察期通过且业务、质量、IT 运维签字后，才提交 G4 申请。

## 8. 停止与回退条件

任一条件出现即停止扩围：

- 图表、明细、Saved 或 Export 在同一 Context 下计数不一致；
- Yield 把 UNKNOWN/ABORT 计入分母或把零分母显示为 0%；
- Program Limit 被当作正式 Spec，或未唯一绑定 Spec/Bin 仍给出正式结论；
- 未批准 Rule 可以通过页面、深链或直接 API 执行；
- 工程 non-owner 读取他人工程数据，或量产只读用户执行管理动作/下载私有原始文件；
- 一个 Dataset 出现多个 Current Version，或分析/Export 改变 Canonical/Current；
- 8-Dataset、Scatter、Spatial、Correlation 超过批准 p95/错误率阈值；
- Export Artifact SHA、ACL、TTL、路径边界或精确清理失败；
- Worker 持续堆积、STAGED intent 无法恢复、服务健康与数据库身份不一致；
- 备份恢复未在批准 RTO/RPO 内完成。

应用候选回退优先切回上一已验证包并关闭 v1.3 Feature Flag；`sql2014_0020+` 的数据兼容与只读边界必须按运维 Runbook 执行。若 Schema 回退会丢弃规则、Saved 或 Export 元数据，则不得执行破坏性 downgrade，应恢复升级前备份。分析/Export 失败不得修改原始文件、Canonical、Dataset Current 或历史 Dataset Version。

## 9. 发布物与安全边界

最终候选必须提供：

- 单一 Release Version、Build A/B ZIP、相同 SHA-256、Manifest 和文件数；
- Archive 路径、CRC、Manifest 内容与禁止文件扫描；
- 解包 `-ValidateOnly`、真实 API launcher/runtime health，以及 Worker 入口随包检查与已记录的 Queue→Worker E2E 证据；目标 TEST 仍需单独复核正式 Worker 服务；
- Migration/预检/安装/启动/状态/停止/Worker/Cleanup 脚本；
- 完成、回归性能、灰度、用户操作、部署备份恢复与回退文档；
- 安全提交记录和远端分支。

不得包含：`F:\data` 原始数据、Golden 原值、`artifacts/` 运行输出、报告产物、日志、缓存、`.remember/`、`.env.runtime.ps1`、账号、密码、Token、数据库连接串、前端 tests/specs/fixtures/mocks 或本机测试启动辅助。

## 10. 发布决定与签字

| 项目 | 当前决定 | 签发条件 |
|---|---|---|
| 仓库候选 | **PASS（本机候选）** | 自动化、SQL E2E、性能、浏览器和双 Release 已完成；不包含 Owner/Data 批准 |
| 本机 G0～G2.5 | **PASS（本机范围）** | 可作为 G3 准备输入，不外推到目标环境 |
| G3 TEST | **NO-GO** | 本机候选签发后，迁移到 SQL Server 2014 SP3+ 目标环境并另行审批环境、数据和人员 |
| Owner-gated 规则 | **全部保持禁用** | 每条规则独立批准、Golden、Activation 和业务验收 |
| G4 生产 | **NO-GO / 未执行** | G3 观察和签字完成后另行提交 |

签发状态：

- 技术证据：`本机 PASS；详见完成报告与回归性能报告`
- DBA/运维：`G3 未签发；目标 SP3+ TEST、备份恢复和正式服务账号待执行`
- Data Owner：`未签发正式 Golden`
- Rule Owner / Quality Validator：`未批准；开发库审批/激活为 0`
- CIO/变更批准：`G3/G4 未申请`
