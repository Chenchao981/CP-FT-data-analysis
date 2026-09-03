# TMS 参数分析首批切片完成报告（2026-08-30）

## 1. 结论

本轮完成了 `TMS_Analytics_Closure_Development_Plan_v1.3` 的首个参数分析技术切片，并在仓库单元测试、前端生产构建、SQL Server 2014 开发库只读对账和真实浏览器任务四个层级完成验证。该切片属于 AC2 前置技术 Spike，用于尽早验证数据合同、SQL 可行性和失败关闭边界，不代表 AC2 已正式启动或关闭。

本轮可以确认：

- `POST /api/v1/datasets/parameter-analysis` 已具备 1～8 个同 Stage Dataset、1～5 个参数、统一 Lot/Wafer/Bin/Result/Source 筛选和描述统计；Tukey BoxPlot、固定分箱 Histogram 已完成技术 Kernel 但默认 Gated，Capability 保持关闭态；
- 所有统计按已解析的精确 `test_item_id + program_version_id + step + sequence + canonical code + condition` 执行，不再按同名参数猜测合并；
- Current、PUBLISHED、Stage、CP Spec、参数身份、正式 Spec 覆盖和 2,000,000 条候选 Measurement 上限均失败关闭；
- 未批准 BoxPlot、Histogram 或 Capability 规则时，API 直调和前端均失败关闭；Capability 未指定规则时稳定返回 `CAPABILITY_RULE_REQUIRED` 且所有指数为 NULL；
- `parameter-analysis` 首端点的图表、统计和响应信封返回 Dataset Context、规范化 Filter Hash、Rule Context、Count、Sampling、Warning 和 UTC `computed_at`；现有 Overview/Compare/Chart/Detail 尚未完成 AC1 统一；
- 真实 CP/FT 结果与独立 SQL 对账一致，验收前后 Canonical/Current 计数及稳定摘要指纹一致。

本轮不是 AC0～AC5 全范围完成，也不是生产发布结论。实施顺序仍以 `AC0 → AC1 → AC2/AC3` 为准；本次 Spike 完成后先返回 AC1。V01～V28 的全部技术范围仍保留；本报告第 8 节列出尚待完成的项目。

## 2. 做了什么

### 2.1 后端合同

- 新增 `POST /api/v1/datasets/parameter-analysis`。
- 请求限制：
  - Dataset 1～8 个，Dataset ID 不重复；
  - 参数 1～5 个，不重复；
  - 首版 `group_by=DATASET`；
  - Lot 50、Wafer 100、Bin 50、Result 4、Source 50；
  - Histogram 分箱 5～100；
  - 每个 Dataset 候选 Measurement 不超过 2,000,000。
- 同维度多值 OR、跨维度 AND；所有筛选值使用绑定参数。
- `parameter-analysis` 首端点返回信封包括：
  - `dataset_context`；
  - 规范化 `filter_summary.filter_hash`；
  - `rule_context`；
  - `capabilities`；
  - Unit `counts`；
  - `sampling_summary`；
  - `warnings`；
  - UTC `computed_at`。
- 当前 `bin_mapping_versions` 尚为空；本切片不做点采样，因此 `sampling_summary` 只表达“未采样”，点数仍为零。这两个字段的系统级闭环仍属于 AC1/AC2 后续工作。

### 2.2 参数身份与正式 Spec 门禁

- 参数身份包含：
  - `test_item_id`；
  - Program Version；
  - Step Code；
  - Sequence/Occurrence；
  - Canonical Parameter Code；
  - Unit；
  - Program Limits；
  - 完整规范化 `text/bias1/bias2` 测试条件。
- 后续 Preflight、Aggregate、Box、Histogram、Formal Spec 和 Subgroup SQL 全部绑定已解析的精确 `test_item_id` 集合。
- 未知 Condition JSON 键、非字符串条件、不同 Bias、不同 Canonical Code、不同 Sequence、同名非分析 Item 均失败关闭。
- Capability 的 Released Spec 从实际候选 Measurement Scope 出发，以 LEFT JOIN 检查每个 Run/Program/Item/Lot/Wafer 的覆盖；部分覆盖、多 Binding、反向限值、单位或条件不一致均不能计算，也不能继续显示为已解析 Released Spec。

### 2.3 统计算法技术 Kernel 与规则门禁

- 描述统计：Row、Numeric、Excluded、Measurement Status、Min、Max、Average、Sample Standard Deviation。
- BoxPlot：
  - SQL Server `PERCENTILE_CONT` 线性 Q1/Median/Q3；
  - Tukey 1.5 IQR；
  - Whisker 使用范围内真实观测值；
  - Min/Max/Outlier Count 独立返回；
  - 方法版本 `TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1`。
- Histogram：
  - 服务端固定等宽分箱；
  - 最后一个上界闭区间；
  - 空分箱补零；
  - 常量列稳定处理；
  - 方法版本 `EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1`。
- BoxPlot 和 Histogram 目前只完成可验证的技术 Kernel；两种固定方法均纳入服务端 Rule Owner 批准集合，默认集合为空。未形成 Owner/Validator/批准日期和正式 Golden 记录时，API 失败关闭并返回 `ANALYSIS_RULE_NOT_APPROVED`，不得作为正式统计口径使用。
- Capability：
  - 技术 Kernel 支持 Overall Ppk 和 Pooled-within Cpk；
  - 无 Rule 时所有 Sigma/Index 为 NULL；
  - 当前默认应用构造的批准集为空，尚无部署配置入口；
  - 确定性单元正例仅通过测试构造器显式注入规则，不等同于计划第 7 节的正式 Golden。

### 2.4 前端

- 新增“参数分析（显式执行）”面板。
- 页面进入、筛选改变和顶部刷新不自动执行参数分析。
- 支持独立选择 1～5 个参数、分析类型和 Overall Result；未获批准的 BoxPlot、Histogram 或显式 Capability Rule 会显示结构化门禁错误。
- Box 图严格使用 `[lower_whisker, q1, median, q3, upper_whisker]`。
- Histogram 只消费后端 Bins，不在浏览器重新分箱。
- 展示：
  - Current+PUBLISHED 验证；
  - Filter Hash；
  - Spec/算法版本；
  - Unit Count；
  - 参数身份和规格来源；
  - Numeric Count 为零；
  - Capability Gate；
  - 结构化 API Error 和显式 Retry；
  - 筛选变化后的结果过期状态。
- 当前 Source 选择器只属于“当前图表与明细 Dataset”。多 Dataset 且选择 Source 时前端明确阻止执行；单 Dataset 正常传递 Source，避免将一个 Dataset 的 Source 静默套用到其他 Dataset。

## 3. 审查发现并关闭的问题

只读代码审查发现并复现以下 P1，均已补回归测试后修复：

1. 跨 Dataset 参数兼容签名遗漏 Canonical Code 和 Sequence。
2. 统计 SQL 只按 Raw Name，可能混入同名非分析 Item。
3. Condition 只比较 `text`，遗漏 FT `bias1/bias2`。
4. Released Spec Inner Join 会静默丢失未绑定 Scope，并把一个 Run 的 Spec 套到其他 Run。
5. 任意 `DATASET_READ` API 调用可显式传 Rule Code 绕过 Owner Gate。
6. Spec Context 不匹配时仍显示 Released Spec Limits。
7. 多 Dataset 将当前明细 Dataset 的 Source 传给全部 Dataset。
8. 前端测试 Fixture 的 Capability 总状态与后端合同漂移。

真实库首轮验收还发现 Canonical 摘要使用浮点 `AVG/STDEV` 作为不变性指纹，在 SQL Server 并行聚合下末位不稳定。验收脚本已改为稳定的 Count、Min/Max ID、Created Time 和逐行字段 `BINARY_CHECKSUM` 汇总；它仍不输出原始值。

## 4. 自动化验证

| 层级 | 结果 |
|---|---|
| 参数分析后端/API/G0 目标集 | 124 passed |
| 后端完整 `tests/unit` | 592 passed，1 skipped，4 个既有 openpyxl `utcnow()` warning |
| 前端完整测试 | 26 files，135 tests passed |
| TypeScript + Vite Production Build | PASS |
| Ruff Check | PASS |
| Ruff Format Check | PASS |
| `git diff --check` | PASS |

前端构建仍有既有体积告警：

- `EChart` chunk 约 1,120 KB，gzip 约 372 KB；
- 主入口 chunk 约 2,374 KB，gzip 约 745 KB。

这不是本轮功能失败，但属于 V28/性能优化待办。

独立复核曾捕获一个既有概率性测试误报：隐私断言对整个 JSON 文本搜索字符串 `7001`，随机 `request_id` 偶然包含该片段时会失败；单例重跑通过。现已改为核对结构化 404 Error Code、公开 Message 和空 Details，不再把随机请求编号当作业务字段泄漏；修复后后端全量 592 个用例一次通过。

## 5. 真实 SQL Server 2014 只读验收

### 5.1 环境和数据规模

- Database：`TMS_G0_DEV`；
- Schema：`sql2014_0019`；
- Engine：Microsoft SQL Server，Product Major 12；
- 候选参数行：169；
- CP、FT 两个 Stage 均选出 Current+PUBLISHED 候选。

不变性快照：

| Scope | Test Run | Unit Result | Measurement |
|---|---:|---:|---:|
| Canonical | 139 | 291,127 | 5,578,114 |
| Current | 50 | 70,887 | 1,251,901 |

- 执行 SQL：90 条；
- 只读门阻断：0 条；
- 执行前后 Count 和稳定摘要 SHA-256：完全一致；
  - Canonical：`aab8f7a278a360c2a2eeae3cc55fe109a6f133cbe871f7ab61489a23be6eb781`；
  - Current：`34222b3ba514c2b39f0a18adcb9076c7ca88e41ad7d7bf33715ed4cdf1691019`；
- 未输出连接串、账号、服务器名、Dataset/Lot/Wafer/参数名或原始 Measurement 值。

### 5.2 数值对账

| Stage | Numeric / Row | 对账内容 | 结果 |
|---|---:|---|---|
| CP | 7,356 / 7,356 | Status、Min/Max/Avg/Stdev、Q1/Median/Q3、Whisker、Outlier、Histogram Sum、Capability Gate | PASS |
| FT | 35,350 / 35,350 | 同上 | PASS |

### 5.3 5 次热运行取样

| Stage | SQL/调用 | 取样范围 | 观察值 |
|---|---:|---:|---:|
| CP | 6 | 5 次热运行 | 3,319.0～3,346.3 ms |
| FT | 6 | 5 次热运行 | 3,980.9～4,079.2 ms |

响应除 `computed_at` 外摘要稳定。`computed_at` 被明确排除在重复响应 Digest 外，但仍在每次响应中返回 UTC 时间。CP 响应摘要为 `9caa708a47a534aa4c3f4d3a5df90a5a28f8e31ef4071d02cecce4a8c4d305be`，FT 为 `706a9078d055702825d776a791d38d86b4dce73558c57716cf60ab544256ef92`。验收基线代码为 `f589687` 加本报告所列暂存变更；最终提交号以 Git 提交记录为准。

## 6. 单 Dataset、免登录 Loopback 浏览器功能冒烟

本机通过 `start_tms_local_test.ps1 -NoBrowser` 启动 API、Route A Worker 和 Vite，身份守卫确认 `TMS_G0_DEV/sql2014_0019`。浏览器任务使用 Loopback 免登录开发管理员，只覆盖单 Dataset 首端点的功能冒烟，不属于认证角色 UAT。

### 6.1 最终代码：FT 描述统计正向冒烟

- 从“历史正式数据”选择真实工程 FT Dataset；
- 页面默认只选择 `DESCRIPTIVE`，选择一个参数后按钮才可执行；
- 显式执行返回 `PARAMETER_ANALYSIS_V1`、Current+PUBLISHED、Filter Hash、Rule Context、Counts、参数身份和描述统计；
- 命中 35,350 行，其中数值 35,088、Missing 262，与服务端结果一致；
- 未出现“统计口径待业务批准”，说明非规则型描述统计可用；页面进入和刷新没有自动执行。

### 6.2 门禁前 Kernel 浏览器证据与最终边界

- Rule Owner 门禁加入前，曾以真实 FT 单参数和 CP 双参数验证 Box/Histogram 渲染、后端分箱消费、Filter Hash、Context 和 Capability 关闭态；两次请求分别为 200，约 4,093.88 ms 和 4,582.79 ms，Console Error/Warning 为 0。
- 这些画面只证明技术 Kernel 和前端消费合同，不是最终开放证据。最终应用构造的批准集合为空，BoxPlot/Histogram 的 API 与服务层负向测试均在执行 SQL 前返回 `409 ANALYSIS_RULE_NOT_APPROVED`。
- 因尚无 Rule Owner/Validator/批准日期和正式 Golden，本轮不做 BoxPlot/Histogram 最终浏览器正向验收；批准后必须重新执行。

页面进入、返回目录和顶部刷新均未自动调用参数分析；只有“执行参数分析”触发请求。

测试完成后，Frontend、Worker 和 API 均正常停止。

## 7. 测试评估

### 7.1 已达到

- 首端点专项自动化和开发库正常样本对账通过；v1.3 的 G0、G1 均未关闭。
- CP/FT 都有真实 Current Dataset 和独立 SQL 证据。
- 首批请求未把全量 Measurement 推到浏览器。
- 所有数据库验收均为只读，且有执行前后数据不变证据。

### 7.2 尚未达到

- 性能计划要求每场景 30～50 次、并发 1/5、p50/p95、错误率、响应大小、逻辑读和执行计划；本轮只有顺序 5 次热运行与少量单 Dataset 浏览器样本。
- 当前单 Dataset、单参数为约 3.3～4.1 秒，超过通用 Chart/Detail 的 3 秒参考线；该端点专属性能门槛仍待 AC0 冻结，还不能宣称性能门关闭。
- 未验证真实 8 Dataset × 5 Parameter、Source Filter、2,000,000 边界和并发压力。
- Browser 为 Loopback 免登录功能验收，不是认证/Owner/角色 UAT。
- 没有 TEST Server、生产服务器、HTTPS、备份恢复或业务签字结论。

### 7.3 v1.3 Gate 状态

| Gate | 当前状态 | 说明 |
|---|---|---|
| G0 | 部分，未关闭 | 首端点目标测试、全量单元测试、前端 Build 和静态检查通过；正式 Golden、全范围 DTO/API/组件和潜在 Migration 尚未完成 |
| G1 | 部分，未关闭 | CP/FT 各一个正常样本数值与数据不变对账通过；筛选、钻取、Saved Analysis、Export 和完整负向矩阵未完成 |
| G2 | 部分冒烟，未关闭 | 单 Dataset、Loopback 免登录 CP/FT 冒烟通过；1～8 Dataset、全部图表、URL/权限/错误矩阵未完成 |
| G2.5 | 未执行 | 尚未完成四类真实角色的本机认证冒烟 |
| G3 | 未执行 | 尚未进入目标 TEST/UAT、30～50 次正式性能、恢复和业务/质量签字 |
| G4 | 未执行 | 尚未进入生产分批、监控和观察期 |

## 8. V01～V28 当前状态与还要完成的工作

状态说明：`已有/部分` 不等于验收关闭；所有未关闭项仍属于 v1.3 本计划，不取消、不转成无日期事项。

| ID | 当前状态 | 还要完成 |
|---|---|---|
| V01 Overview KPI | 部分 | 迁移到统一 Filter/Rule/Count Context，完成全/单/多选与下钻对账 |
| V02 Yield 趋势 | 部分 | Lot/Wafer/Batch 多选、顺序/时间合同、Zoom 和点选钻取 |
| V03 Bin/Pareto | 部分 | 统一 Bin/Result Filter、版本化 Bin Mapping、Pareto 联动 |
| V04 风险摘要 | 部分/Gated | 汇总能力缺失、超限、低能力/PAT 风险并固定批准规则 |
| V05 明细 | 部分 | Evaluation、Source Row、Rule Version、Wide/Long、统一 Filter Hash |
| V06 图表钻取 | 部分 | 稳定 Drilldown Key 和 Die/Unit/Measurement Drawer |
| V07 BoxPlot | **技术 Kernel 完成，整体部分/Gated** | Rule Owner 批准记录、正式 Golden、多 Dataset/多参数性能、钻取与业务验收 |
| V08 Histogram/Distribution/Normal | **Histogram 技术 Kernel 完成，整体部分/Gated** | Histogram Rule Owner 批准和正式 Golden；Normal Fit、正态性解释、性能与钻取 |
| V09 Scatter | 部分 | 统一参数对/分组/来源合同、确定性采样、Brush 和钻取 |
| V10 Correlation | 未开始 | 后端矩阵、Pairwise 缺失、方法/样本版本和 Heatmap |
| V11 参数趋势 | 未开始 | Unit/Wafer/Lot/Batch 有序序列、单/多参数趋势 |
| V12 Cpk/Ppk | **Kernel 原型、确定性单元正负例和关闭态完成，Gated** | 正式 Golden、Rule Registry、Owner 批准、最小样本/稳定性/正态性/Missing/Retest Policy、业务验收 |
| V13 Bin Wafer Map | 部分 | 统一 Filter、坐标能力、重复坐标和 Drawer |
| V14 Parameter Heatmap | 未开始 | 参数值、Spec 状态、颜色域、坐标门禁和 Die 钻取 |
| V15 Multi-Wafer Overlay | 未开始 | 坐标对齐、Count/Rate 聚合、Wafer 显隐 |
| V16 Parameter + Fail Bin Overlay | 未开始 | 同 Unit/Filter 的参数评价与 Fail Bin 联结 |
| V17 Zone | 未开始/Gated | Edge/Center/Quadrant 几何规则、样本/良率/参数统计 |
| V18 FT 多参数 Scatter | 部分 | Test Sequence/Batch/Tester/Program 分组、采样和异常点钻取 |
| V19 正式 PAT | Quick 独立通道已有，正式未完成 | 复用批准 PAT 引擎，固定 Dataset/Filter/Rule，异常点追溯 |
| V20 SYL/SBL/Yield/Fail Bin | 部分/Gated | 公式、输入角色、版本和正式质量摘要 |
| V21 条件/机台/程序/批次比较 | 未开始 | 维度可用性、兼容性合同和服务端聚合 |
| V22 SPC I-MR | 未开始/Gated | Subgroup、控制限、Run Rule、排除记录、图表和下钻 |
| V23 Margin/OOS | 未开始/Gated | 单/双边距离、单位、NULL、评价版本和下钻 |
| V24 好/坏品与 Bin 共现 | 未开始/Gated | 总体定义、共现合同、稀疏限制和 Heatmap/Pareto |
| V25 Wafer Summary | 未开始 | 固定 Wafer 统计、动态参数列、分页和导出 |
| V26 Saved Analysis | 未开始 | 服务端固定 Dataset Version/Filter Hash/Rule/Owner，恢复与过期告警 |
| V27 分析导出/报告 | 分析 Export 未开始；Cleaner Export 已有但语义不同 | PNG/CSV/XLSX/BIN-TXT/HTML/PDF Job、SHA、TTL、权限和审计 |
| V28 显示控制 | 部分 | Y/颜色范围、Brush、图例、PNG、点数限制和前端 Bundle 拆分 |

## 9. 下一批执行顺序

1. **返回计划主序列并关闭 AC1**：将 Overview、Compare、Detail、现有 Chart 迁到同一 Filter Hash/Rule/Count Context，修复 Source Identity 的 Tester/Run Fallback，关闭图表/明细/导出口径分裂。本轮 AC2 Spike 不构成顺序变更批准。
2. **AC2 剩余关系分析**：完成 V08 Normal Fit、V09 Scatter、V10 Correlation、V11 Trend，并做确定性采样和钻取。
3. **性能专项**：为首端点取得执行计划/逻辑读，冻结真实 8×5 Golden，做 30～50 次并发 1/5 的 p50/p95；超过同步门槛时改异步 Job，不隐性采样总体。
4. **AC3 空间分析**：V14～V18、V21。
5. **AC4 Owner Gate 与交付**：Rule Registry、V12/V19～V24、Saved Analysis、Export/Report。
6. **AC5 与后续 Gate**：V01～V28 技术缺口为零，完成本机 G0～G2.5 与发布候选；认证 UAT 和 TEST Server 属于 G3，生产前 Gate 后再进入 G4。

## 10. 可复现入口

自动化：

```powershell
.\.conda-env\python.exe -m pytest tests\unit -q
Set-Location frontend
npm test
npm run build
```

真实开发库只读验收：

```powershell
& {
    . .\.env.runtime.ps1
    $env:PYTHONIOENCODING = 'utf-8'
    .\.conda-env\python.exe scripts\g0\verify_v13_parameter_analysis.py --warm-runs 5
    exit $LASTEXITCODE
}
```

本机浏览器功能验收：

```powershell
.\scripts\windows\start_tms_local_test.ps1 -NoBrowser
.\scripts\windows\get_tms_local_test_status.ps1
.\scripts\windows\stop_tms_local_test.ps1
```

禁止将 `.env.runtime.ps1`、数据库连接串、原始数据、运行日志或 `artifacts/` 纳入提交或报告。
