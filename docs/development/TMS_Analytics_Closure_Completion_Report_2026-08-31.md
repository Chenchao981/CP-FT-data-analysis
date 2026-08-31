# TMS v1.3 分析能力闭环完成报告

- 报告日期：2026-08-31
- 目标版本：TMS v1.3 Analytics Closure
- 开发分支：`codex/auth-rbac-frontend`
- 数据库验证基线：SQL Server 2014 / `TMS_G0_DEV` / `sql2014_0023`
- 范围：AC1～AC5 的仓库技术实现、本机开发库验证、前端操作入口和发布候选准备
- 当前报告状态：**AC1～AC5 本机技术关闭；G0～G2.5 本机候选 PASS**
- 生产状态：**未上线；G3/G4 未执行**

## 1. 结论

AC1～AC5 的计划内仓库技术能力已进入同一套后端权威分析合同和 React 前端工作台，并已在本机开发库、真实 CP/FT Current Dataset、真实浏览器、四角色认证矩阵、正式性能探针和双构建发布候选上完成闭环。VDMOS 没有成为独立菜单、原始数据解析器或第二套统计引擎；可复用的交互能力位于“历史正式数据 → 分析”，按 Overview、Detail、Parameter、Spatial、Quality、Delivery 六组操作。

已经确定的核心边界如下：

- 正式分析唯一事实链仍是 `test.test_run -> test.unit_result -> test.measurement`；Saved Analysis 和 Export 只保存 Context、状态与 Artifact，不复制 Measurement。
- Yield、UNKNOWN/ABORT、Pareto、分布、采样、空间聚合、质量评价和导出内容均由后端生成，前端不重算业务结论。
- 图表、明细、钻取、保存和导出共享版本化 Dataset、规范化 Filter、Rule Context 与稳定身份，不再用筛选首项或“代表记录”替代聚合证据。
- Spec 与 Bin 使用物化后的版本化评价事实；无匹配、多匹配、非法值、未评价均显式失败关闭，不使用 Program Limit 冒充正式 Spec。
- 开发库当前没有真实统计规则审批或激活记录；本轮没有伪造审批。BoxPlot、Histogram/Normal、Correlation、Cpk/Ppk、PAT、SYL/SBL、SPC、Margin、Zone、Bin 共现等能力保留 `ANALYSIS_RULE_NOT_APPROVED` 门禁。
- 本机 G0～G2.5 只能证明仓库、开发库和本机浏览器候选，不等于目标 TEST 的 G3，更不等于生产 G4。

AC1～AC5 可签发“本机技术关闭”，G0～G2.5 可签发本机候选 PASS，其中 AC4 的 Owner-gated 正式统计能力继续保持关闭。该签发只覆盖仓库、本机开发库和本机浏览器，不代表 Data/Rule Owner 批准，也不代表 G3 TEST 或 G4 生产准入。

## 2. 做了什么

### 2.1 统一分析工作台与操作路径

- 四个固定入口继续保留：工程 CP、工程 FT、量产 CP、量产 FT。
- Dataset 选择后进入六组工作台：Overview、Detail、Parameter、Spatial、Quality、Delivery；高成本分析由用户显式触发并懒加载。
- 统一保存 Dataset Version、Lot/Wafer/Bin/Result/Source/Tester/Program/Condition/Parameter、页面、排序、评价筛选和显示状态；刷新、深链、后退、Saved Restore 与 Export 使用同一 `ANALYSIS_VIEW_STATE_V1`。
- 图表点、趋势点、Pareto 分组、Zone、Wafer 和质量异常均使用完整成员键钻取，不用首条记录假装整个分组证据。

### 2.2 后端权威分析与数据治理

- 新增 Overview、Parameter Analysis、Parameter Relationship、Spatial、Quality Evaluation、Wafer Summary、Saved Analysis 与 Analytics Export 的强类型 API/服务。
- 新增 `sql2014_0020` Analytics Governance、`sql2014_0021` Export Lifecycle、`sql2014_0022` Quality Rule Types、`sql2014_0023` Analytics Performance Indexes；不改写历史 Migration。
- 在 Canonical 写入事务内物化正式 Spec Measurement Evaluation 和 Bin Mapping Evaluation；重复执行幂等，失败不发布似是而非的正式结论。
- 正式 Spec 解析按 Run 事件时间和版本绑定，不随“当前时间”或后来 active 状态漂移；所有正式运算符必须显式存在并通过校验。
- Scatter/SPC/Margin 等大点集采用后端确定性采样，并保留全部正式超规格/规则命中点；响应返回原始点数、返回点数和保留点数。
- Saved Analysis 写入要求外层 `SAVED_ANALYSIS_V1` 与内层 `ANALYSIS_VIEW_STATE_V1`，保存 Dataset/Filter/Rule/显示配置及修订历史；读取兼容历史合同。
- Analytics Export 支持 Queue、Worker、Artifact SHA、TTL、取消、过期、权限与审计；格式合同覆盖 PNG、CSV、XLSX、BIN-TXT、HTML、PDF，具体格式由模板和 Scope 白名单决定。

### 2.3 正式 PAT 复用

- AC4/V19 使用版本化 `FORMAL_PAT_SHARED_ENGINE_ADAPTER_V1`，算法为 `PAT_SHARED_IQR_1_35_V1`，复用成熟共享引擎而不是重写第二套 PAT。
- IQR 除数固定为 1.35，Median±6Sigma，输出 6 位小数；规则创建和执行必须匹配冻结的算法 Manifest SHA。
- 技术对账覆盖 4 组正常/插值/零离散/负值与小数向量；已有真实 Quick 证据为 520 文件、6,813,800 解析行、23 个参数，23/23 满足冻结公式。
- 技术对账不改变 Owner Gate；没有三方批准和 Activation 时正式 PAT 仍禁用。

### 2.4 发布与运行边界

- 发布构建排除前端测试/fixture/mock、开发机本地启动辅助、原始数据、运行输出、日志、缓存、账号和秘密。
- 生产包保留 preflight、Migration、runtime health、安装/启动/状态、Worker 和受控清理入口。
- 解包后通过包内 launcher 启动 API 的真实 smoke，而不是只在源码树运行单元测试。
- 外部运行配置与服务账号凭据不进入源码、ZIP、日志或命令行；目标机仍需使用 Windows 受保护凭据。

## 3. AC1～AC5 验收矩阵

状态定义：`本机技术关闭` 表示实现、开发库、自动化、性能、发布候选和已记录前端技术证据已形成闭环；`Owner/Data Gate` 不表示删除能力，而表示正式入口必须保持关闭或按数据条件降级。

| AC | 计划交付 | 当前实现 | 已取得证据 | 最终关闭条件 / 当前状态 |
|---|---|---|---|---|
| AC1 统一 Context | Overview、Detail、现有 Chart、钻取、导出同 Context；四入口与六分组 | 本机技术关闭 | 四个固定入口和六分组已在真实浏览器操作；统一 View State、完整聚合钻取、正式评价/来源证据、Current-page 排序与筛选导出完成对账 | 正式账号与目标 TEST UAT 属 G3；**本机技术关闭** |
| AC2 参数基础分析 | V07～V11、V28；Distribution、BoxPlot、Scatter、Correlation、Trend | 本机技术关闭；受规则或数据条件启用 | CP/FT Descriptive、FT Scatter/Relationship、强类型 Rule Reference、单位/条件/Spec 兼容、后端确定性采样和规则拒绝均有真实数据/浏览器证据 | Owner 规则继续禁用；正式性能归 AC5；**本机技术关闭** |
| AC3 空间与 FT 质量 | V14～V18、V21；Heatmap、Overlay、Composite、Zone、FT 多参数与条件比较 | 本机技术关闭；坐标/Bin/Spec/Rule 数据门控 | CP Dataset 113/V2 的 Parameter Heatmap、Wafer Summary 正向通过；Bin Map 因无版本化 Mapping 明确失败关闭；FT UNKNOWN/Yield=NULL 展示正确 | Multi-Wafer/FT 多源正式性能归 AC5；无能力数据继续空态/门控；**本机技术关闭** |
| AC4 规则统计与交付 | V04、V12、V19～V27；规则注册、Saved、Export | 本机技术关闭；Owner Gate 保持关闭 | 正式 PAT Adapter 对账，规则/激活门禁，Saved 创建/恢复/修订/逻辑删除，以及 Export Job #5 的 Queue→Worker→Artifact→下载前端闭环均已验证 | 开发库审批/激活为 0，不造审批；未获批准的正式规则继续拒绝；**本机技术关闭，Owner Gate 关闭** |
| AC5 全量回归与交付 | 全量、SQL、性能、G0～G2.5、双构建、报告、回退 | 本机技术关闭 | 后端 1,011 passed、前端 237 tests、Schema 0023、Spec/Bin/Export Lifecycle E2E、只读 SQL、13 场景正式性能、浏览器/四角色矩阵和双构建全部 PASS | 双包各 275 个 Manifest payload 文件（ZIP 276 entries）/ 798,680 Bytes / 同 SHA；残留进程和临时目录为 0；**本机技术关闭，G3/G4 未执行** |

AC0 的源盘点已经完成，但 Data Owner 选定的正式 Golden 小集、逐规则 Owner/Validator 批准与业务签字仍是外部门禁。用户本轮要求的 AC1～AC5 技术开发不能被该门禁取消；同时也不能绕过 AC0 把技术测试写成正式规则批准。

## 4. V01～V28 状态

状态说明：

- `技术可用`：代码和前端入口已实现；仍按 Dataset 权限与能力响应运行。
- `数据门控`：实现存在，但缺少坐标、Bin Mapping、正式 Spec、PASS/FAIL 或维度时明确降级/禁用。
- `Owner 门控`：实现存在，但必须提供已批准且激活的精确 Rule Code/Version；开发库当前无此批准。

| ID | 能力 | 技术状态 | 正式入口状态与门禁 |
|---|---|---|---|
| V01 | 总量、PASS/FAIL、UNKNOWN/ABORT、Yield | 技术可用 | Yield 分母仅 PASS+FAIL；零分母返回 NULL，不补 0% |
| V02 | Lot/Wafer/Test Batch Yield 趋势 | 技术可用 | 无可靠时间/顺序维度时数据门控；聚合点使用全部成员下钻 |
| V03 | Bin 分布与 Pareto | 技术可用 | 版本化 Bin Mapping 数据门控；未映射原始 Bin 不冒充正式含义 |
| V04 | 风险摘要 | 技术可用 | DQ/能力风险可用；Cpk/PAT/SBL 等统计风险受 Owner 门控 |
| V05 | CP Die / FT Unit / Measurement 明细 | 技术可用 | 正式评价、来源行和 Rule Version 按数据可用性展示；不以 Program Limit 替代 Spec |
| V06 | 图表钻取 | 技术可用 | 单点和聚合分组均使用稳定成员键；无证据身份时失败关闭 |
| V07 | BoxPlot | 技术已实现 | **Owner 门控**：精确规则未批准时不可执行正式分析 |
| V08 | Histogram / Distribution / Normal Fit | 技术已实现 | Descriptive 可用；Histogram/Normal 方法与规则受 Owner 门控 |
| V09 | Scatter | 技术可用 | 参数身份、单位、条件、正式 Spec 与大点采样按数据门控 |
| V10 | Correlation | 技术已实现 | **Owner 门控**：方法、缺失处理和最小样本规则需精确批准 |
| V11 | 单值/多值参数趋势 | 技术可用 | 顺序维度、参数身份和单位冲突按数据门控 |
| V12 | Cpk/Ppk | 技术已实现 | **Owner 门控**：Spec、标准差、最小 n、稳定性等未批准不启用 |
| V13 | Bin Wafer Map | 技术可用 | 坐标完整性与版本化 Bin Mapping 双重数据门控 |
| V14 | Parameter Heatmap | 技术可用 | CP 坐标、参数值和正式 Spec 评价按数据门控 |
| V15 | Multi-Wafer Composite / Overlay / Stack | 技术可用 | 多 Wafer、坐标对齐和完整成员清单按数据门控 |
| V16 | Parameter + Fail Bin Overlay | 技术可用 | 同 Unit 的正式 Spec 评价与版本化 Bin Mapping 数据门控 |
| V17 | Edge/Center/Quadrant Zone | 技术已实现 | **Owner 门控 + 数据门控**：Zone V2 规则和可信坐标均必需 |
| V18 | FT 测试序号/批次多参数 Scatter | 技术可用 | Tester/Program/Batch/Condition 维度存在性和参数身份数据门控 |
| V19 | 正式 PAT 参数表与异常点 | 技术已实现 | **Owner 门控**；共享引擎 Adapter 已对账，未批准时正式 API 禁用 |
| V20 | SYL/SBL、Yield、Fail Bin | 技术已实现 | Yield 基础口径按源事实；SYL/SBL **Owner 门控**，Fail Bin 受 Mapping 数据门控 |
| V21 | 条件/机台/程序/批次比较 | 技术可用 | 维度缺失或条件不兼容时数据门控，不从文件名猜测 |
| V22 | SPC I-MR 与动态规则 | 技术已实现 | **Owner 门控**：顺序、Phase、控制限和 Run Rule 必须批准 |
| V23 | Margin / Out-of-Spec | 技术已实现 | **Owner 门控 + 正式 Spec 数据门控**；单/双边和 NULL 规则不默认 |
| V24 | 好/坏品分布与 Bin 共现 | 技术已实现 | **Owner 门控 + Bin Mapping 数据门控**；共现分母不默认 |
| V25 | Wafer Summary | 技术可用 | CP/Wafer 身份、参数与正式评价按数据门控；支持服务端分页/排序/导出 |
| V26 | Saved Analysis | 技术可用 | 固定 Version/Filter/Rule/View State；非 Current、规则替代或权限变化显式告警 |
| V27 | PNG/CSV/XLSX/BIN-TXT/HTML/PDF Report | 技术可用 | 模板/Scope/Stage/权限白名单；大结果由 Job/Worker 生成并校验 SHA/TTL |
| V28 | 图表显示控制 | 技术可用 | Zoom、Brush、图例、轴域和颜色域只影响显示，不改变后端统计总体 |

该表是“实现与门禁状态”，不是 Rule Owner 签字表。开发库审批为 0 时，任何 Owner-gated 行都不得改写为“业务已启用”。

## 5. 真实数据与 Golden 边界

### 5.1 已确定

- 对 `F:\data\CP和FT源数据` 做了只读盘点：204 个目录、2,970 个文件、6,336,287,297 Bytes；没有在源目录写缓存或中间文件。
- `RELATIVE_PATH_SIZE_V1` 摘要为 `69e1a83956004b647adcc45677f207201144bf11b02e6963a0eee7256a003c4a`；它只证明相对路径和大小清单，不是逐文件内容 SHA。
- 已验证 Route A 正向范围仅包括 CP HUAHONG/JETECH/LION 与 FT RIYUEXIN/RIYUEGUANG 的获批 DC 路径；其他厂家/格式仍需 Profile/Adapter Gate。
- 当前受控开发库已有 8 个同 Stage FT Current Dataset（105～112）用于本轮多 Dataset 分析候选：42 Runs、662,799 Units、11,930,382 Measurements。
- 当前受控 CP Dataset 113/V2 来自 25 Wafer、3,875 Units、50,375 Measurements；由前端实际触发 Batch 161 / Job 199 / Run 164 重处理并成为 Current，V1 已 Superseded。Parameter Heatmap 和 Wafer Summary 有正向数据，Bin Map 因没有版本化 Mapping 按设计门控。
- 日月新/日月光 DC 没有源 PASS/FAIL/Bin 语义时，`overall_result=UNKNOWN`、Yield=NULL；不得从 Spec 反推 PASS 或填 0%。

### 5.2 尚未确定或不得外推

- Data Owner 尚未把上述源池签发为完整正式 Golden；逐文件内容 SHA、Cleaner Release、Expected、容差、Owner/Validator 仍需冻结。
- FT 有正式 PASS/FAIL/Bin 的已批准 Golden 仍缺；不能拿日月新 DC 的测量值计算后伪造成源结果。
- Dataset 113/V2 的 UI 重处理、Current 切换、旧版 Supersede 和正式 Spec Evaluation 已形成开发库证据；该证据仍不是 Data Owner 对完整正式 Golden 的签字。
- 其他 CP/FT 目录不能因“文件能打开”自动进入 Canonical 正向范围。

## 6. 已取得的验证证据

| 层级 | 结果 | 说明 |
|---|---|---|
| 后端全量单元/合同 | `1,011 passed, 4 skipped, 16 warnings` / 43.01 s | 4 个 Skip 均为当前 Windows 账号缺少创建 symlink 权限（WinError 1314 类环境条件），非功能断言失败 |
| 前端串行全量 / Build | `48/48 files`、`237/237 tests` / 635.80 s；Build 26.19 s PASS | Vitest 使用单 Worker；仅有 jsdom pseudo-element `getComputedStyle` 警告和 Vite chunk-size 提示 |
| Python compileall | PASS | backend 与 scripts 编译通过 |
| 变更 Python lint/format | PASS（约定范围） | 所有变更 Python 通过 Ruff `E4/E7/E9/F`（忽略项目既有路径注入 `E402`）；87 个新增 Python 文件通过 format check。全仓严格 Ruff 仍受既有 `E402` 等债务影响，仓库 format check 仍报告 112 个历史文件未格式化，未冒充全仓 PASS |
| Spec Evaluation SQL E2E | PASS | PASS/FAIL/NO_MATCH/CONFIG_AMBIGUOUS/NOT_EVALUATED/INVALID_VALUE 六状态；二次运行 6/6 幂等；外层回滚后无残留 |
| Bin Mapping SQL E2E | PASS | MATCHED/NO_MATCH/CONFIG_AMBIGUOUS 三状态；二次运行 3/3 幂等；外层回滚后无残留 |
| v1.1 兼容只读 SQL | PASS | 186 条只读语句、0 blocked，Canonical/Current/Catalog 前后不变 |
| v1.3 Parameter 只读 SQL | PASS | 168 条只读语句、0 blocked；CP/FT Descriptive 独立对账；规则审批/激活前后均为 0，伪造精确规则稳定拒绝 |
| Schema 复核 | PASS | Alembic Current/Head 均为 `sql2014_0023`；Canonical 总量未因 Migration 改变 |
| 正式 PAT Adapter | PASS | 4/4 技术向量与共享引擎一致；真实 Quick 摘要 23/23 参数公式一致；Owner Gate 未绕过 |
| 真实浏览器 UAT | PASS（本机技术范围） | 四个固定 CP/FT 入口及六分组完成操作；CP 113/V2 的 Overview/Detail/Parameter/Spatial/Quality/Delivery、FT 105～112 的 UNKNOWN/Yield=NULL、Detail、Descriptive、Relationship，以及正向/空态/规则拒绝均已核对 |
| 四角色认证矩阵 | PASS（本机技术范围） | SYSTEM_ADMIN、CP_ENGINEER、FT_ENGINEER、MANAGER_VIEWER 的可见菜单、允许路径和直接 URL 403 已核对；退出登录后回到登录页；4 个临时账号已禁用并清空角色 |
| Saved Analysis | PASS（本机技术范围） | 创建 R1、恢复、修订 R2、逻辑删除均由真实前端完成；携带伪规则的保存请求被拒绝 |
| Analytics Export Job #5 | PASS（本机技术范围） | 从 Delivery 前端提交 CURRENT_PAGE CSV，Worker 成功生成 50 行 Artifact；大小 8,069 Bytes，SHA-256 `5257879395d0911b0cddc4b2bd95b7d98c5556767207afe13091c487f5e2bf97`，本地 Artifact 哈希一致且前端下载可用 |
| 浏览器控制台 | PASS（最终新页面） | 最终代码重启后重新核对 CP 113/V2 和 FT 112/V1；0 error，仅 2 条导航时 ECharts disposed warning |
| 正式 13 场景性能 | PASS | warmup 2、并发 1/5 各 30 次；13/13 达标、0 error、0 blocked；Canonical 计数不变；证据 `artifacts/ac5_performance_20260831/v13_performance_formal_30_final.json` |
| 最终双构建 / 解包 API smoke | PASS | `v1.3-analytics-closure-rc1`；A/B 均含 275 个 Manifest payload 文件（ZIP 276 entries）、798,680 Bytes、SHA-256 `dffd339152e48e66008dcbf2a50b4c8d15f15bc59d20b934481eb61f58940568`；CRC/Manifest/秘密与禁止路径扫描、包内 launcher 和真实 ready 检查 PASS；残留进程/临时目录均为 0 |
| `git diff --check` | PASS | 仅行尾转换提示，不是补丁空白错误 |

本机签发证据已经回填完整。详细的 13 场景 p50/p95/max/SQL 数、Skip 明细、C5 single-flight 边界和发布归档检查见《TMS v1.3 分析闭环回归与性能测试报告》。

## 7. 不确定、限制与门禁

1. SQL Server 开发库为 12.0.5000.0，低于 G3 要求的 SQL Server 2014 SP3+；开发库结果不得外推到目标服务器。
2. 当前 8-Dataset 解决了 v1.2 的 Coverage SKIP 数据规模问题；正式 13 场景性能已按固定工作负载完成并 PASS。C5 中 Parameter Descriptive 与纯 Scatter Relationship 的相同请求使用同进程 single-flight；多 Uvicorn 进程或异构请求仍分别访问数据库，结果不能外推为五条不同查询的数据库容量。
3. 开发库统计规则审批/激活记录为 0；这不是缺陷掩盖，而是必须保持的治理事实。Rule Owner、Technical Owner、Quality Validator 没有共同批准前，相关正式按钮/API 必须失败关闭。
4. 本轮已取得用户视角的真实浏览器操作证据和四角色矩阵，不以组件测试代替前端任务验收；最终性能/后端变更加载后的 CP、FT 新浏览器页与控制台 smoke 也已复核。
5. AC0 正式 Golden、目标 TEST、HTTPS、服务账号、备份恢复、容量、安全专项、正式账号 UAT、业务/质量签字均不在本机代码测试中自动完成。

## 8. 可复现入口

下列命令不包含密码或连接串；数据库命令要求操作者在本机受控运行配置中预先配置环境。

```powershell
# 后端全量
.\.conda-env\python.exe -m pytest tests\unit -q
.\.conda-env\python.exe -m compileall -q backend scripts

# 前端全量和生产构建
Set-Location frontend
npx vitest run --maxWorkers=1
npm run build
Set-Location ..

# 开发库只读/事务 E2E
. .\.env.runtime.ps1
.\.conda-env\python.exe scripts\g0\verify_sql2014_schema.py
.\.conda-env\python.exe scripts\g0\verify_v11_functional_sql_readonly.py
.\.conda-env\python.exe scripts\g0\verify_v13_parameter_analysis.py
.\.conda-env\python.exe scripts\g0\verify_spec_evaluation_materialization_sql_e2e.py
.\.conda-env\python.exe scripts\g0\verify_bin_mapping_materialization_sql_e2e.py
.\.conda-env\python.exe scripts\g0\verify_analytics_export_lifecycle_sql_e2e.py

# 性能：先 smoke，再正式并发 1/5
.\.conda-env\python.exe scripts\g0\verify_v13_analytics_closure_performance.py --smoke --warmup 0 --iterations 1 --concurrency 1
.\.conda-env\python.exe scripts\g0\verify_v13_analytics_closure_performance.py --warmup 2 --iterations 30 --concurrency 1 5

# 本机前端操作环境
.\scripts\windows\start_tms_local_test.ps1 -NoBrowser
.\scripts\windows\get_tms_local_test_status.ps1
.\scripts\windows\stop_tms_local_test.ps1
```

所有真实 SQL/E2E 必须先验证数据库名 `TMS_G0_DEV`、Schema `sql2014_0023` 和 SQL Server 身份；写入型夹具只能使用外层回滚或随机唯一身份并在结束后独立复核为 0。

## 9. 下一步

1. 由 Data Owner、Rule Owner、Quality Validator 冻结正式 Golden 和逐规则批准；批准前保持 Owner Gate。
2. 在 SQL Server 2014 SP3+ 独立 TEST 建立 G3 环境，配置 HTTPS、正式服务账号和正式角色，并完成备份恢复演练。
3. 在 G3 固定 Golden 上复跑 30～50 次性能、正式账号浏览器 UAT、安全/容量与运维验收；本机 p95 不直接作为生产容量。
4. Job #5 作为审计记录及其未过期 Artifact 由 TTL 清理流程处理，不手工破坏审计链；不得删除 `F:\data` 或整个 `artifacts` 根。
5. G3 观察、业务/质量签字和回退演练通过后，才可提交 G4 生产变更申请。

## 10. 签发栏

| 角色 | 结论 | 日期/证据 |
|---|---|---|
| 技术负责人 | **AC1～AC5 本机技术关闭** | 自动化、SQL E2E、正式性能、浏览器与双 Release 证据已回填 |
| 数据/业务 Owner | 未签发正式 Golden | 待指定受控样本与 Expected |
| Quality Validator | 未批准动态统计规则 | 开发库审批/激活为 0 |
| G0～G2.5 Gate | **PASS（本机候选范围）** | 不包含 Owner/Data 签字，不外推到目标 TEST 或生产 |
| G3/G4 | **NO-GO / 未执行** | 目标环境与生产流程未启动 |
