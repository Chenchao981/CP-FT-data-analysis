# TMS v1.3 分析能力闭环开发计划

- 计划日期：2026-08-30
- 目标版本：TMS v1.3 Analytics Closure
- 当前业务基线：`docs/business/TMS_Business_Requirements_v0.3.md`
- 当前功能基线：TMS v1.2 Functional RC
- 当前数据库基线：SQL Server 2014 / Alembic `sql2014_0019`
- 能力参考：`VDMOS_Tool_v8.9.html` 中适用于当前 Canonical 数据的图表与最终用户体验
- 发布边界：本计划可在仓库、本机和开发库完成 G0-G2.5；目标 TEST、正式账号 UAT 和生产发布仍分别属于 G3、G4

## 1. 目标与完成定义

本计划把已经可运行的正式数据目录、比较、明细和少量核心图表，推进为覆盖 CP/FT 日常分析的完整闭环。VDMOS 不作为独立菜单、独立数据源或第二套计算引擎迁入 TMS；其适用能力按以下业务分组进入现有“历史正式数据 → 分析”主路径：

1. Overview：Yield、Bin、Pareto、趋势和风险摘要；
2. Detail：CP Die、FT Unit、Measurement 与来源钻取；
3. Parameter：BoxPlot、Distribution、Scatter、Correlation、趋势、Cpk/Ppk；
4. CP Spatial：Wafer Map、Parameter Heatmap、Overlay、复合失效和区域比较；
5. FT Quality：多参数 Scatter、PAT、SYL/SBL、Fail Bin 和条件对比；
6. Quality Rules：SPC I-MR、Margin、Out-of-Spec 和规则版本；
7. Delivery：Wafer Summary、Saved Analysis、Report 和受控 Export。

“v1.3 分析能力闭环完成”必须同时满足：

- 本计划列出的适用能力有明确状态；已纳入 v1.3 的功能完成后端、前端、Golden、性能和真实浏览器验收；
- 图表、明细和导出使用同一组 Dataset Version、规范化筛选和规则上下文；
- CP/FT 的统计语义、缺失值、Spec、Bin、单位和采样均失败关闭，不由前端猜测；
- PAT、Cpk/Ppk、SBL、SPC 等动态统计只有在业务 Rule Owner 批准后才开放；
- 不重复开发 v1.0-v1.2 已关闭的上传、Current、权限、重复上传、生命周期和 Quick PAT 基础事项；
- 每个功能均可从结论钻取到正式 Current Dataset、Canonical 明细、来源文件和规则版本；
- 形成日期化完成报告、回归/性能报告、浏览器 UAT 证据和可回退发布候选。

仓库交付完成不等于生产上线。G3 需要目标 SQL Server 2014 SP3+ TEST、正式角色账号、真实规模 Golden 和业务签字；G4 还需要生产安全、备份恢复、容量、变更窗口和观察期。

## 2. 事实、历史边界与不重复范围

### 2.1 当前事实

截至本计划冻结时，以下事实作为开发起点：

- v0.3 是当前业务基线；其未修改的分析规则继续继承 v0.2；
- 唯一正式明细链仍为 `test.test_run -> test.unit_result -> test.measurement`；
- 开发库与代码的唯一 Alembic head 为 `sql2014_0019`；
- 工程普通用户只读本人正式数据，量产 `Current + PUBLISHED` 向 `DATASET_READ` 用户共享；管理动作仍由 Owner/Admin 控制；
- 同 SHA 重复上传形成独立 Receipt、Batch、Job、Run 和 Dataset；Current 唯一性以 Dataset 为边界；
- 当前比较合同支持 1-8 个同 Stage Dataset，以及 Lot、Wafer、Bin、最多 20 个参数筛选；
- 当前结构化明细支持服务端分页，单页最多 200 行；
- 当前 CP 图表已有 Wafer Yield 趋势、Bin Pareto、Bin Wafer Map 和 Bin 分布；
- 当前 FT 图表已有单参数器件 Scatter、LSL/USL、测试条件和确定性采样；采样保留超规格点，普通点最多约 10,000 个；
- 当前多 Dataset 比较返回 PASS/FAIL/UNKNOWN/ABORT、Yield，以及参数 minimum/maximum/average；
- Quick PAT 已形成隔离闭环，但其 Workspace/Artifact 不是正式 Measurement，也不等于正式 PAT 分析已完成；
- v1.2 的 8-Dataset 无参数和 5 参数性能场景因兼容数据不足仍为 Coverage SKIP；
- G0/G1 已 PASS，G2 为 PASS WITH LIMITS；目标 TEST/UAT G3 和生产 G4 未执行。

### 2.2 当前分析合同的已知缺口

现有功能可以继续复用，但不能被误写成完整分析闭环：

1. 比较和明细接受 Lot/Wafer/Bin/参数多选；现有图表请求只使用当前 Dataset，以及 Lot、Wafer、参数的首项；Bin 不进入图表查询。
2. 当前图表响应没有完整、机器可对账的 `filter_summary`、`rule_context`、`sampling_summary` 和能力降级原因。
3. CP 已有 Bin Map，但没有 Parameter Heatmap、参数/Fail Bin Overlay、多 Wafer 复合失效和 Zone 分析。
4. FT 已有单参数 Scatter，但没有正式 PAT 表/异常点、SYL/SBL、条件/机台/程序/批次对比。
5. BoxPlot、Histogram/Normal、Correlation、单值/多值趋势、Cpk/Ppk、SPC、Margin 和 Out-of-Spec 尚未形成逐项后端合同与验收证据。
6. 现有 Cleaner 最新版导出属于 `EXPORT_LATEST` 生命周期动作，不等于分析筛选结果、图片、报告或 Saved Analysis 已完成。
7. v1.1/v1.2 完成报告证明了业务路径、比较、明细、少量图表、Quick 和质量看板，不构成全部 VDMOS 适用能力的验收清单。

### 2.3 v0.6/v0.7 的使用边界

- `TMS_Implementation_Roadmap_v0.6.md` 与 v0.6 前端/图表文档只作为能力来源、技术原则和决策演进证据，不作为当前完成状态。
- `TMS_Development_Plan_v0.7_Route_A.md` 只作为 A2/A3/A4 历史阶段拆分和 Vertical Slice 验收参考；其中已失效的 FIRST_BATCH Spec 规则不得复活。
- 当前 Spec 规则是 Lot 级证据绑定；无法证明相同 Spec 时禁止合并分析。
- 本计划不重开旧阶段，不按 v0.6/v0.7 的百分比重新验收，而是从 v1.2/`sql2014_0019` 现状增量开发。

### 2.4 已关闭、不得重复开发的事项

以下能力继续回归但不作为 v1.3 新开发工作包：

- 四个固定入口：工程 CP、工程 FT、量产 CP、量产 FT；
- Source Catalog、相对路径、Manifest、Cleaner Release/SHA 和原始数据只读；
- Route A SQL Queue、Worker、Canonical Writer、staged + 幂等 finalize；
- Dataset-scoped Current、工程私有、量产共享、Owner/Admin 管理边界；
- 同 SHA 顺序/并发重复上传独立分析和身份切换清缓存；
- Current Catalog、1-8 Dataset 选择、URL 深链、服务端分页明细；
- Product 补录、最新 Cleaner 非变异导出、显式重清洗和逻辑归档；
- Quick PAT Q0、Workspace 容量/TTL/清理基础；
- 管理质量 KPI 和现有趋势下钻。

## 3. 不变设计原则

1. **One Canonical**：正式分析只读取 `test.*` Canonical 和与其版本化关联的 Spec/Bin/来源元数据。
2. **Current + Published**：共享正式分析只允许读取当前已发布 Dataset Version；历史版本仅 Owner/Admin 在明确历史入口读取，不混入 Current 比较。
3. **Backend Authority**：Yield、Bin、分位数、分布、相关性、Cpk/Ppk、PAT/SBL、SPC、采样、Spec 匹配、单位和排除规则由后端执行。
4. **No Silent Filter**：不得静默删除 IQR 异常、NULL、超规格点或修改值；任何排除必须在规则与响应中显式返回。
5. **Same Context**：图表、比较、明细和导出必须共享规范化 Context 与稳定 Hash。
6. **Fail Closed**：未知 Stage、单位、Spec、Bin、规则、参数身份或不兼容 Dataset 不生成看似有效结果。
7. **Capability Degradation**：缺 Wafer/X/Y、Bin、Spec 或 PASS/FAIL 时只关闭依赖能力，并明确原因，不阻断其他可证明的分析。
8. **Progressive Cost**：首次进入不自动执行全部高成本统计；用户确认 Dataset 与筛选后按分组懒加载或点击计算。
9. **No Vendor UI Fork**：厂家差异留在 Cleaner/Adapter/Metadata，不为每个厂家复制分析页面。
10. **Server Saved State**：正式 Saved Analysis 写服务端并固定 Dataset Version、Filter、Rule；Local Storage 只保存主题、列宽等非业务偏好。

## 4. 统一后端分析合同

### 4.1 请求合同

所有新增或重构后的分析端点共享以下语义合同；具体 DTO 必须是强类型，禁止用任意 SQL、任意表达式或前端公式字符串：

```json
{
  "datasets": [
    {"dataset_id": 1, "version_no": 1}
  ],
  "filters": {
    "lot_ids": [],
    "wafer_ids": [],
    "bin_codes": [],
    "overall_results": [],
    "source_ids": [],
    "tester_ids": [],
    "program_versions": [],
    "test_conditions": []
  },
  "parameters": [],
  "rule_context": {
    "spec_context_id": null,
    "evaluation_rule_codes": []
  },
  "display_request": {
    "max_points": null,
    "histogram_bin_request": null
  }
}
```

约束：

- `datasets` 为 1-8 个，必须同 Stage；CP 多 Dataset 必须证明 Spec 兼容；
- 每个请求重新执行 Principal、Owner/Domain、Current、PUBLISHED 和参数兼容性校验；
- 空数组表示授权范围内全部，不得由前端展开成巨量 ID；
- 参数身份必须包含名称/代码、Occurrence/Step、单位、测试条件和来源 Item 身份；仅显示文本相同不等于同一参数；
- `display_request` 只能影响可逆显示或经过批准的服务端采样，不能改变统计总体；
- 图表、明细、导出不再各自解释筛选；同一页面只维护一个 Canonical Filter Context。

### 4.2 响应信封

每个统计响应必须返回：

```json
{
  "dataset_context": {
    "resolved_datasets": [],
    "test_stage": "CP",
    "current_published_verified": true
  },
  "filter_summary": {
    "normalized_filters": {},
    "filter_hash": "sha256"
  },
  "rule_context": {
    "spec_versions": [],
    "bin_mapping_versions": [],
    "evaluation_rule_versions": []
  },
  "capabilities": [],
  "counts": {
    "input_units": 0,
    "included_units": 0,
    "excluded_units": 0,
    "missing_measurements": 0
  },
  "sampling_summary": {
    "sampled": false,
    "method": null,
    "original_points": 0,
    "returned_points": 0,
    "preserved_out_of_spec_points": 0
  },
  "series": [],
  "warnings": [],
  "computed_at": "UTC timestamp"
}
```

对不适用能力返回稳定 capability code 和中文原因，不返回空图伪装成功。计划使用的失败关闭错误至少包括：

- `ANALYSIS_VERSION_NOT_CURRENT`；
- `ANALYSIS_STAGE_INCOMPATIBLE`；
- `ANALYSIS_SPEC_INCOMPATIBLE`；
- `ANALYSIS_PARAMETER_INCOMPATIBLE`；
- `ANALYSIS_CAPABILITY_UNAVAILABLE`；
- `ANALYSIS_RULE_NOT_APPROVED`；
- `ANALYSIS_RULE_VERSION_REQUIRED`；
- `ANALYSIS_COORDINATE_CONTRACT_INVALID`；
- `ANALYSIS_FILTER_LIMIT_EXCEEDED`；
- `ANALYSIS_RESULT_TOO_LARGE`。

### 4.3 服务边界

计划采用按分析语义分离的强类型端点；路由名称在 AC1 API Review 冻结，但不得退化为一个任意 `analysis_type + options` 端点：

| 服务 | 主要输出 |
|---|---|
| Overview | Unit、PASS/FAIL/UNKNOWN/ABORT、Yield、Bin、DQ/能力风险、Lot/Wafer/Batch 趋势 |
| Parameter Distribution | Histogram、Normal Fit、BoxPlot、分位数、缺失/超限计数 |
| Parameter Relationship | Scatter、Correlation、单值/多值趋势 |
| CP Spatial | Bin Map、Parameter Heatmap、Overlay、Composite Failure、Zone |
| FT Quality | 多参数 Scatter、Fail Bin、PAT/SYL/SBL、条件/机台/程序/批次对比 |
| Rule Evaluation | Cpk/Ppk、PAT、SBL、SPC、Margin、Out-of-Spec |
| Detail Drilldown | CP Die、FT Unit、Measurement、来源行、评价状态和规则版本 |
| Saved Analysis / Export | 固定 Dataset/Filter/Rule 的服务端保存、报告与 Artifact Job |

首个落地端点冻结为 `POST /api/v1/datasets/parameter-analysis`，合同版本 `PARAMETER_ANALYSIS_V1`：

- `datasets` 1-8 个、`parameters` 1-5 个，`group_by` 首版只允许 `DATASET`，各 Dataset 分组计算且绝不合并原始值；
- 筛选支持 Lot、Wafer、Bin、PASS/FAIL/UNKNOWN/ABORT 和 Source，多值继续遵循同维度 OR、跨维度 AND；
- `analyses` 首批支持 `DESCRIPTIVE`、`BOX_PLOT`、`HISTOGRAM`、`CAPABILITY`；Box 使用线性分位数和 Tukey 1.5 IQR 实际观测须线，Histogram 只返回服务端固定分箱；
- 每个 Dataset 的同步候选 Measurement 上限为 2,000,000，超过即 `ANALYSIS_WORKLOAD_LIMIT_EXCEEDED`，不把全量值推给浏览器；
- `CAPABILITY` 未携带显式版本化 `rule_code` 时，Ppk/Cpk 均保持 `NOT_REQUESTED`，返回 `CAPABILITY_RULE_REQUIRED` 且所有能力指数为 NULL；不得默认选择规则；
- 只有 Current + PUBLISHED 的 CP/FT 可以进入；Stage、CP Spec、参数单位/限值/测试条件或显式能力规则下的正式 Spec 不兼容时失败关闭。

现有 Dataset Chart、Compare 和 Detail API 在 AC1 中兼容迁移。旧 API 只有在前端和测试全部切换后才可弃用；不得在一个版本中无提示改变已有响应语义。

### 4.4 统计与采样边界

- Histogram 的分箱算法、边界闭开规则和极值处理由 Rule Version 固定；
- BoxPlot 返回原始样本数、缺失数、Q1/Median/Q3、Whisker、异常点数量和算法版本；默认不删除异常点；
- Scatter 超过点数上限时由后端确定性采样，必须保留超规格点并返回采样摘要；
- Correlation 由后端返回矩阵、有效样本数、缺失配对规则和方法版本；
- Wafer Map 单片可以返回全点；Multi-Wafer 由后端聚合，不把所有 Measurement 推入浏览器；
- PAT/Cpk/Ppk/SBL/SPC 计算结果必须固定 Dataset、Filter、Spec/Bin、Rule Version 和排除记录；
- 前端只做缩放、Brush、显隐、排序、颜色和显示精度等可逆交互。

## 5. VDMOS 适用能力闭环矩阵

状态定义：

- **已有**：v1.2 已有且只需非回归；
- **部分**：已有可复用实现，但合同或验收未覆盖完整目标；
- **待开发**：当前没有可验收闭环；
- **Owner Gate**：允许先完成版本化计算器、关闭态页面和 Golden 技术验证；只有 Rule Owner 批准后才允许在正式业务入口启用，未批准时不得代选默认公式或宣称业务可用；
- **独立通道**：Quick/Workspace 已有同名能力，但不能替代正式分析。

| 编号 | 适用能力 | 当前状态 | 后端闭环 | 前端交付 | 阶段 / 优先级 |
|---|---|---|---|---|---|
| V01 | 总量、PASS、FAIL、UNKNOWN/ABORT、Yield | 部分：摘要/比较已有 | 统一 Overview Context；分母仅 PASS+FAIL；零分母为 NULL | Overview KPI、口径、样本数、未知占比和下钻 | AC1 / P0 |
| V02 | Lot/Wafer/Test Batch Yield 趋势 | 部分：CP Wafer 趋势已有 | 多选筛选和多 Dataset 聚合；返回时间/顺序口径 | 支持全选/单选/多选、Zoom、点选钻取 | AC1 / P0 |
| V03 | Bin 分布与 Pareto | 部分：CP 已有，筛选未统一 | Bin/Result 进入统一筛选；Bin 含义来自版本化 Mapping | Pareto、累计占比、Bin 卡片、明细联动 | AC1 / P0 |
| V04 | 风险摘要 | 部分：质量 KPI 已有 | 能力缺失、超限、低 Cpk/PAT 风险按批准规则汇总 | Overview 风险卡和原因下钻 | AC4 / P1，统计项受 Gate |
| V05 | CP Die / FT Unit / Measurement 明细 | 已有基础分页 | 补齐评价状态、来源文件/行、Rule Version；与图表共享 Filter Hash | Wide/Long 切换、固定身份列、服务端分页/排序/过滤 | AC1 / P0 |
| V06 | Die/Unit 图表钻取 | 部分 | 稳定 drilldown key，返回单位、Spec、Bin、来源和评价 | 图表点选打开 Drawer，再到 Measurement | AC1 / P0 |
| V07 | BoxPlot | 技术 Spike：Kernel 已实现，正式能力未关闭 | 服务端分位数/Whisker/异常点合同；Rule Owner 批准前默认 Gated | 参数或 Dataset 分组 BoxPlot、样本/缺失提示 | AC2 / GATED P1 |
| V08 | Histogram / Distribution / Normal Fit | 技术 Spike：Histogram Kernel 已实现；Quick Workspace 不替代正式能力 | 服务端分箱、分布摘要、Normal Fit 和规则版本；Rule Owner 批准前默认 Gated | Histogram、拟合线、Spec/缺失/超限标记 | AC2 / GATED P1 |
| V09 | Scatter | 部分：FT 单参数已有 | 统一多选 Context；CP/FT 参数对、来源和分组强类型；确定性采样 | X/Y 参数选择、分组、Brush、规格线、钻取 | AC2 / P1 |
| V10 | Correlation | 待开发 | 后端矩阵、Pairwise 缺失规则、样本数和方法版本 | Heatmap、阈值筛选、点击进入 Scatter | AC2 / P1，方法受 Gate |
| V11 | 单值/多值及参数趋势 | 待开发 | 按 Unit 序号、Wafer、Lot、Batch 的有序序列合同 | 单/多参数趋势、单位冲突阻断、图例控制 | AC2 / P1 |
| V12 | Cpk/Ppk | Owner Gate | 规则版本、Spec 绑定、最小样本数、标准差/单边规则 | 指标、置信/适用性说明、分布和明细钻取 | AC4 / GATED P1 |
| V13 | BIN Wafer Map | 部分：单片 Bin Map 已有 | 统一筛选、坐标完整性、重复坐标和 Map 能力响应 | Bin Map、Tooltip、Die Drawer、图例 | AC1 / P0 |
| V14 | Parameter Heatmap | 待开发 | 参数值、Spec 状态、颜色域统计；可信坐标必需 | 参数选择、颜色范围、Spec Overlay、Die 钻取 | AC3 / P1 |
| V15 | 多 Wafer 复合失效 / Overlay / Stack | 待开发 | 服务端坐标对齐、计数/比例聚合和 Wafer 清单 | 复合失效 Map、Wafer 显隐、聚合口径说明 | AC3 / P2，P2 不代表可取消 |
| V16 | Parameter + Fail Bin Overlay | 待开发 | 参数评价与 Fail Bin 在同一 Unit/Filter Context 联结 | 参数热力与 Fail Bin 图层切换/叠加 | AC3 / P1 |
| V17 | Edge/Center/Quadrant Zone 分析 | 待开发 | Zone 几何和边界规则版本；返回区域样本/良率/参数统计 | Zone Map、区域对比和区域明细 | AC3 / P2，规则受 Gate |
| V18 | FT 测试序号/批次多参数 Scatter | 部分：单参数 Scatter 已有 | 多参数、来源/机台/程序/批次维度；抽样与超限保留 | 多系列 Scatter、条件筛选、异常点钻取 | AC3 / P1 |
| V19 | 正式 PAT 参数表与异常点 | 独立通道：Quick PAT 已有；正式能力未完成 | 复用批准 PAT 引擎，固定 Dataset/Filter/Rule；异常点可追溯 | PAT 表、阈值、异常数、Unit/Measurement 钻取 | AC4 / GATED P1 |
| V20 | SYL/SBL、Yield、Fail Bin | 部分：Yield/部分 Bin 已有 | SYL/SBL 公式、分组、输入角色和版本；不得从缺失字段猜测 | 质量摘要、趋势、Fail Bin Pareto 和明细 | AC4 / GATED P1 |
| V21 | 按条件、机台、程序、批次比较 | 待开发 | 维度可用性和兼容性合同；服务端聚合 | 对比选择器、分组图和能力限制说明 | AC3 / P1 |
| V22 | SPC I-MR 与动态规则 | Owner Gate | Subgroup、控制限、Run Rule、排除和版本合同 | I-MR 图、规则命中标记、异常点下钻 | AC4 / GATED P1 |
| V23 | Margin / Out-of-Spec | Owner Gate | Spec 距离、单位、单/双边、NULL 规则和版本 | Margin 分布、超限率、参数/Unit 钻取 | AC4 / GATED P1 |
| V24 | 好品/坏品分布与 Bin 共现 | 待开发 | 分组总体、共现定义、样本数和稀疏矩阵限制 | 对比分布、共现 Heatmap/Pareto | AC4 / P2，定义受 Gate |
| V25 | Wafer Summary | 待开发 | 服务端 Wafer 级固定统计与动态参数元数据 | 固定身份列、横向参数、服务端分页/导出 | AC4 / P1 |
| V26 | Saved Analysis | 待开发 | 保存 Dataset Version、Filter Hash、Rule Context、图表配置、Owner 和版本 | 保存/另存/恢复；显示过期或非 Current 状态 | AC4 / P1 |
| V27 | PNG/CSV/XLSX/BIN-TXT/HTML/PDF Report | 部分：Cleaner 导出已有但语义不同 | 分析 Export Job、模板版本、SHA、TTL、权限和审计 | 当前图图片导出；大结果/报告走 Job 状态与下载 | AC4 / P1 |
| V28 | 图表显示控制 | 部分 | 后端保持事实不变；颜色域/点数限制写入响应 | Y 轴、颜色范围、Zoom、Brush、图例、PNG；不得改变总体 | AC2-AC4 / P2 |

## 6. 统计规则 Owner 门禁

### 6.1 强制批准记录

任何动态统计规则进入正式 API 或生产 Feature Flag 前，必须形成一份可版本化批准记录，至少包含：

- `rule_code`、显示名称和适用 Stage/厂家/产品/参数范围；
- Business Rule Owner、Technical Owner、Quality Validator 和批准日期；
- 输入字段、单位、Spec/Bin 优先级和无匹配/多匹配行为；
- Retest、PASS/FAIL/UNKNOWN/ABORT、缺失值和重复 Unit 处理；
- 公式、分位数/标准差定义、分组维度、最小样本数和舍入；
- 排除规则、异常点是否仅标记或从统计量中排除；
- Golden 输入、expected 输出、容差和与原工具对账结果；
- 规则版本、Effective From/To、替代关系和回退版本；
- 页面应展示的算法说明、警告和不适用条件。

未取得批准时，后端返回 `ANALYSIS_RULE_NOT_APPROVED`，前端显示“统计口径待业务批准”，不得隐藏入口后仍通过 URL 调用，也不得临时在 React 中实现公式。

允许在开发库用显式测试注入验证固定算法 Kernel、SQL 可行性和失败关闭，但必须同时满足：生产构造的批准集合默认为空；报告标注为技术 Spike；不得将测试构造器正例称为正式 Golden；不得据此把 V01～V28 状态写成正式完成。

### 6.2 各方法必须关闭的决策

| 方法 | Rule Owner 必须批准的关键点 |
|---|---|
| Histogram/Normal | 分箱算法、边界、极值、拟合方法、正态性解释边界 |
| BoxPlot | Q1/Q2/Q3 算法、Whisker 倍数、异常点仅标记还是排除 |
| Correlation | Pearson/Spearman、Pairwise/Listwise 缺失处理、最小样本数 |
| Cpk/Ppk | Sample/Population 标准差、短期/长期、单边 Spec、最小 n、目标值 |
| PAT | Q1/Median/Q3、上下倍数、Spec 与 PAT 层关系、Retest/缺失和分组范围 |
| SBL/SYL | 输入文件角色、批次/参数维度、阈值、Yield/Fail Bin 关系 |
| SPC I-MR | 子组、移动极差、控制限、Western Electric/自定义 Run Rules、重算窗口 |
| Margin/OOS | Margin 定义、单/双边、单位换算、边界相等、NULL/无 Spec 行为 |
| Zone | Edge 宽度、Center/Quadrant 几何、无完整 Wafer 外形时的处理 |
| Bin 共现 | 同一 Unit/Wafer/Lot 的共现定义、Pass Bin 排除、分母和稀疏阈值 |

VDMOS HTML 和既有 Python 工具是候选公式证据，不等于业务批准。确认与现有生产工具一致后优先通过 Adapter/共享引擎复用，不另写第二套算法。

## 7. Golden 验收数据矩阵

真实数据始终放在受控目录，仓库只保存 Manifest、SHA、expected 摘要和脱敏证据。每组 Golden 必须记录 Dataset/Version、来源 SHA、Cleaner Release、Canonical 数量、Spec/Bin/Rule Version 和预期图表摘要。

### 7.1 CP Golden

1. 单 Lot、单 Wafer、完整 X/Y/Bin/Spec：基础 Yield、Pareto、Bin Map、Heatmap 和 Die 钻取。
2. 单 Lot、多 Wafer：趋势、Overlay、Composite Failure 和 Zone。
3. 多 Lot、逐 Lot 相同 Spec 正向：合并与分 Lot 结果可对账。
4. 多 Lot、不同 Spec 负向：比较和统计失败关闭。
5. 缺 X/Y：普通参数/明细可用，所有空间能力明确关闭。
6. 重复坐标、坐标缺口或非法坐标：按批准坐标合同阻断或明确降级。
7. PASS/FAIL/UNKNOWN/ABORT 混合与零已知分母：Yield 口径和空值正确。
8. 参数缺 Spec、单边 Spec、单位冲突和测试条件冲突：能力与错误码正确。

### 7.2 FT Golden

1. 有 PASS/FAIL/Bin 的正式 FT：Yield、Fail Bin、分布、Scatter 和明细。
2. 无 PASS/FAIL 的日月新类样本：Yield 保持 NULL，不显示 0%。
3. 多源文件、多机台、多程序、多测试条件：分组与兼容性提示正确。
4. 测量缺失、超规格点、失败后未测参数：NULL 和状态不被补值。
5. 超过 10,000 点的参数：采样确定性、全部超规格点保留、原始/返回点数可对账。
6. PAT/SBL/SPC 每个批准规则至少一组正常、一组边界、一组负向 Golden。
7. 同显示名但不同 Occurrence/Step/Bias/Unit/Condition 的参数：禁止错误合并。

### 7.3 跨 Dataset、权限与交付 Golden

- 至少 8 个同 Stage、兼容 Spec 的 Current Dataset，用于无参数和 5 参数比较；
- CP/FT 混选、非 Current、Draft、Spec 不兼容、参数不兼容的负向集；
- 工程 A、工程 B、量产查询用户、Admin 四角色访问和直接 URL 矩阵；
- Saved Analysis 的正常恢复、Dataset 非 Current、Rule 被替代和权限变化场景；
- 每类 Export 的 Filter Hash、模板版本、行数、文件 SHA、权限、TTL 和过期下载；
- 同一筛选下图表计数、明细总数、导出总数和直接 SQL expected 完全一致。

## 8. 自动化、性能与浏览器验收

### 8.1 自动化测试

| 层级 | 必须覆盖 |
|---|---|
| Domain | Filter 规范化/Hash、能力判断、统计规则版本、采样摘要、错误码 |
| Backend Unit | 每个图表聚合、缺失值、边界、负向和 Rule Gate |
| SQL Integration | Current/PUBLISHED、Owner/Domain、Spec/参数兼容、分页、聚合和执行计划 |
| Golden | 原工具/批准 expected 与 TMS 输出逐项对账 |
| API Contract | 请求上限、强类型响应、Filter/Rule/Sampling Context、权限失败关闭 |
| Frontend Component | 全/单/多选、能力降级、图表/明细联动、URL 恢复、错误与空态 |
| Browser | 从历史正式数据选择 Dataset 到图表、钻取、保存和导出的完整任务链 |
| Release | TypeScript、前后端全量、生产 Build、Archive/Manifest/秘密扫描、launcher smoke |

每个新功能先建立失败用例，再实现；每个阶段先运行目标测试，再运行全量回归。测试不得只断言 HTTP 200 或页面存在，必须断言业务数值、上下文和钻取身份。

### 8.2 性能基线

沿用 v1.2 门槛，并补足当时未覆盖的场景：

| 场景 | 开发/TEST 候选门槛 |
|---|---:|
| 普通 UI 点击反馈 | <= 300 ms |
| 单 Dataset Chart/Detail 热查询 | <= 3 s |
| 质量/Overview 热查询 | <= 3 s |
| 8 Dataset、无参数比较 | <= 3 s |
| 8 Dataset、5 参数比较 | <= 5 s |
| 单参数大 Scatter 首次可视结果 | <= 5 s，返回采样摘要 |
| 单 Wafer Map | <= 3 s，返回全部有效坐标点 |
| Multi-Wafer 聚合/Correlation | 由 AC0 用真实规模冻结，目标不超过 5 s；超限时改为异步 Job |

性能验收要求：

- 使用固定 Golden 规模，记录 Dataset/Unit/Measurement/参数/Wafer 数量；
- 每场景至少 30-50 次，分别测并发 1 和 5 的 p50/p95、错误率、响应大小和 SQL 语句数；
- 记录冷/热查询、逻辑读、执行计划和是否发生全表扫描；
- 大 Scatter 记录原始点、返回点、超规格点和采样稳定性；
- 浏览器记录首屏、图表交互和 ECharts/主包体积；优先懒加载和按需引入，不提前建设复杂缓存平台；
- 开发库结果不得外推为目标 TEST 或生产结论。

### 8.3 真实浏览器任务验收

每个 Stage 至少完成以下任务：

1. 从工程/量产固定入口进入“历史正式数据”，不手填 Dataset ID；
2. 选择 1 个、多个和 8 个兼容 Dataset，刷新、后退和深链后状态恢复；
3. Lot/Wafer/Bin/参数分别验证全选、单选、多选，图表、明细和导出结果一致；
4. 能力缺失时显示原因，例如无坐标、无 Bin、无 Spec、无 PASS/FAIL 或规则未批准；
5. 高成本图表必须由用户触发，切换 Tab 不自动并发计算全部分析；
6. Hover、Zoom、Brush、图例和颜色范围不改变权威统计；
7. 点击图表点进入 Die/Unit/Measurement，并继续看到 Source、Release、Rule Version；
8. 保存分析后重新打开，固定 Context 可复现；版本失效时明确提示而非静默切到最新；
9. Export Job 的创建、状态、下载、过期和权限拒绝均有可恢复反馈；
10. 工程 A/B、量产查询用户、Admin 真实账号矩阵通过，无跨用户缓存；
11. 代表性桌面分辨率真实渲染，控制台 warning/error 为零或有批准的已知例外；
12. 浏览器结果与 Golden/SQL expected 对账，不只做截图验收。

## 9. 实施阶段、依赖与关闭条件

```text
AC0 证据与规则冻结
  -> AC1 统一 Context / 现有图表一致性
      -> AC2 参数基础分析
      -> AC3 CP 空间 + FT 质量
          -> AC4 获批统计 + Saved Analysis / Report / Export
              -> AC5 全量回归、G0-G2.5 与发布候选
                  -> G3 目标 TEST/UAT
                      -> G4 生产分批
```

AC2 与 AC3 可在 AC1 关闭后并行；共享 DTO、Filter、Rule 或 Export 合同不得由两个实现分叉。

为降低后续返工，可在 AC1 关闭前做 AC2/AC3 的有界技术 Spike，但它不构成阶段顺序变更：成果必须默认 Gated、只用于验证合同和技术风险，完成后返回 AC1，且不得关闭对应 AC、G0/G1 或正式业务能力。

### AC0：基线、Golden 与 Rule Owner 冻结

交付：

- 本计划和 V01-V28 能力台账；
- 当前 API/页面/数据库事实快照；
- CP/FT Golden Manifest、8-Dataset 兼容数据计划和负向矩阵；
- Histogram、BoxPlot、Correlation、Cpk/Ppk、PAT/SBL、SPC、Margin、Zone、Bin 共现的 Owner 清单；
- 统一 Analytics Request/Envelope、错误码和 API ADR；
- 当前 ECharts/主包体积、查询执行计划和性能基线。

关闭条件：每个能力有 Owner、优先级、Golden、API/页面落点和验收人；未批准统计可继续实现显式规则版本和关闭态，但不得自行选择业务默认值、不得在正式入口启用，也不得以技术测试代替 Owner 签字。

### AC1：P0 统一分析 Context 与现有能力一致性

交付：

- 比较、图表、明细、钻取和导出共享同一多选 Filter Context；
- 消除图表仅取筛选首项以及 Bin 不作用于图表的现状；
- 响应统一返回 Dataset、Filter、Rule、Capability、Count 和 Sampling Context；
- CP Yield/Bin/Pareto/Bin Map、FT Scatter 和 Unit 明细迁移到新合同；
- Current/PUBLISHED、工程私有、量产共享和参数/Spec 兼容性不回退；
- AnalyticsWorkbench 按 Overview/Detail/Parameter/Spatial/Quality/Delivery 分组并懒加载。

关闭条件：现有图表无数值回归；全/单/多选下图表、明细、比较和导出可对账；刷新/深链可恢复；全部 P0 自动化和浏览器用例通过。

### AC2：P1 参数基础分析

交付：V07-V11，以及 V28 的基础显示控制；后端完成 Distribution、BoxPlot、Scatter、Correlation 和趋势合同，前端复用统一 ECharts Wrapper。

关闭条件：每个图表有正常、边界和负向 Golden；单位/条件冲突失败关闭；缺失和异常不静默删除；大点集有确定性采样摘要；图表可钻取明细。

### AC3：P1/P2 CP 空间与 FT 质量

交付：V14-V18、V21；复用 V13 基础 Bin Map，新增 Parameter Heatmap、Overlay、Composite Failure、Zone、多参数 FT Scatter 和条件/机台/程序/批次对比。

关闭条件：无可信坐标时空间能力关闭；单/多 Wafer 聚合与 SQL expected 对账；FT 多源参数身份和测试条件不串用；每个高级图表完成性能与真实浏览器验收。

### AC4：Owner Gate 统计与交付

交付：V04、V12、V19-V27。所有能力完成可测试的技术实现；每个已批准方法独立完成业务验收，未批准方法完成版本化计算器、Golden、关闭态和启用门禁后保持禁用，待 Owner 批准即启用，不把能力永久取消或转入无日期的后续。Saved Analysis 和分析 Export 只保存 Context/结果 Artifact，不建立第二套 Measurement 事实。

关闭条件：全部能力的代码、Golden、负向测试和关闭态完成；获批规则还须 Rule Owner 签字并通过业务公式验收，未获批规则须证明默认不可调用且返回稳定门禁原因；Saved Analysis 可复现且版本变化明确告警；报告和导出与图表/明细同 Context，Artifact SHA/TTL/权限/审计完整。

### AC5：全量回归、灰度和交付

执行：

1. 目标单元、合同、SQL、Golden、组件和浏览器测试；
2. 后端/前端全量、TypeScript、Build、Migration head 和回滚检查；
3. `TMS_G0_DEV / sql2014_0019+` 真实 SQL 对账；
4. 8-Dataset、并发 1/5、大 Scatter、Multi-Wafer 和 Correlation 性能；
5. 本机免登录功能 UAT；
6. 管理员、工程用户、量产查询用户认证模式非回归；
7. 双构建、Archive/Manifest/CRC/秘密/禁止路径扫描和 launcher smoke；
8. 完成报告、回归/性能报告、灰度报告和回退说明；
9. 安全提交并推送源码、Migration、测试、文档和发布物清单。

关闭条件：计划范围 P0/P1/P2 的技术实现缺口为零；所有 V01-V28 都有“技术完成且已启用”或“技术完成但 Owner Gate 禁用”的证据，不允许以“转后续”代替本计划交付；仓库/开发库 G0-G2.5 通过。

## 10. Schema、兼容与回退策略

- `sql2014_0019` 是本计划起点，绝不改写历史 Migration；
- 纯查询和图表优先不增加持久表；只有 Rule Registry、Saved Analysis、Evaluation/Export 元数据确有必要时才新增 `sql2014_0020+`；
- 新表只保存规则、Context、审计和 Artifact 元数据，不复制 `test.measurement` 形成第二事实源；
- API 先增量提供新合同，前端切换和兼容测试完成后再弃用旧 Chart DTO；
- 每个分析分组使用 Feature Flag；Rule Gate 未通过、Golden 失败或性能超限时可以单独关闭，不影响正式数据目录和基础明细；
- 回退应用版本时必须明确新 Saved Analysis/Rule Version 的只读兼容边界；
- 分析失败、Export 失败或规则计算失败不得改变 Dataset Current、Canonical 行数、补录值或原始文件；
- 任何临时物化或 Artifact 都受受管根、容量、TTL、DryRun 和精确清理约束。

## 11. Gate 与最终 Definition of Done

| Gate | 环境 | 通过条件 | 不通过/回退条件 |
|---|---|---|---|
| G0 | 静态与自动化 | DTO/API/SQL/组件/Golden 目标测试，全量、TypeScript、Build、Migration 全绿 | 任一口径错误、P0、构建或 Migration 失败 |
| G1 | 本机开发库 | 真实 CP/FT 数值、筛选、钻取、Saved/Export 和数据不变对账 | 图表与明细不一致、未知值被补零、Current 漂移 |
| G2 | 本机浏览器 | 四入口、1-8 Dataset、全部图表分组、错误/空态、URL、权限和控制台验收 | 手填内部 ID、首项筛选、静默降级、越权或页面死链 |
| G2.5 | 本机认证冒烟 | 四类角色合法路径可用，既有拒绝行为不回退 | 合法用户 403、跨工程 Owner 泄漏、管理动作误显示 |
| G3 | 目标 TEST/UAT | SQL Server 2014 SP3+、正式账号、30-50 次性能、备份恢复、业务/质量签字 | Rule 未签、8-Dataset 未测、性能/恢复未达阈值 |
| G4 | 生产分批 | 单厂家/单 Stage 起步、监控、容量、安全、变更审批和观察期通过 | 触发批准的口径、错误率、性能或恢复回滚阈值 |

只有同时满足以下条件，才能声明 v1.3 分析能力仓库交付完成：

- AC0-AC5 关闭，V01-V28 技术实现缺口为零；Owner Gate 只区分正式启用状态，不缩减实现范围；
- 所有已开放统计具有 Rule Owner、版本、Golden 和页面说明；
- 图表、明细、导出在同一 Context 下可对账；
- CP/FT、单/多 Dataset、全/单/多选、缺失能力和权限矩阵均有真实浏览器证据；
- 8-Dataset 和大数据性能覆盖不再 SKIP；
- 新 Schema 若存在，只通过 `sql2014_0020+` 前进并有回滚说明；
- 原始数据、输出、日志、缓存、账号、秘密和 `.remember/` 不进入提交或发布包；
- 日期化完成、回归/性能和灰度报告明确“做了什么、确定的、不确定的、验证、下一步”；
- 交付物通过归档检查、真实 launcher smoke 和哈希复核。

生产上线完成还必须额外满足 G3/G4、目标服务器、HTTPS、正式账号、备份恢复、持续运行、业务 UAT 和正式签字；不得用本机 G0-G2.5 替代。

## 12. 计划交付物

- 本开发计划及 V01-V28 状态台账；
- Analytics API/DTO/错误码 ADR；
- Rule Owner 批准记录和版本化算法说明；
- Golden Manifest、expected 摘要和负向矩阵；
- 后端分析服务、前端分组页面、Saved Analysis 与 Export Job；
- 自动化测试、性能脚本和浏览器任务用例；
- `TMS_Analytics_Closure_Completion_Report_YYYY-MM-DD.md`；
- `TMS_Analytics_Closure_Regression_Performance_Report_YYYY-MM-DD.md`；
- `TMS_v1.3_Analytics_Gray_Release_Report_YYYY-MM-DD.md`；
- 更新后的用户指南、API 合同、运维/回退说明；
- 可复现发布候选包、Manifest、SHA-256 和安全提交记录。

## 13. 主要依据

- `docs/business/TMS_Business_Requirements_v0.3.md`；
- `docs/business/TMS_Business_Requirements_v0.2.md` 中未被 v0.3 修改的历史查询与图表要求；
- `docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/TMS_09_User_Workflow_and_Chart_Baseline.md`；
- `docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/TMS_08_Reference_Project_Capability_Mapping.md`；
- `docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/TMS_04_Frontend_Architecture_React_AntD.md`；
- `docs/development/TMS_Frontend_Functional_Development_Plan_v1.1_2026-08-29.md`；
- `docs/development/TMS_Frontend_Functional_Completion_Report_2026-08-30.md`；
- `docs/development/TMS_Data_Visibility_Duplicate_Upload_Development_Plan_v1.2_2026-08-30.md`；
- `docs/development/TMS_Data_Visibility_Duplicate_Upload_Completion_Report_2026-08-30.md`；
- `docs/development/TMS_v1.2_Regression_Performance_Test_Report_2026-08-30.md`；
- `docs/development/TMS_v1.2_Functional_Gray_Release_Report_2026-08-30.md`；
- `docs/development/TMS_VDMOS_Reference_Algorithm_Audit_2026-08-30.md`；
- 当前 `backend/app/domain/datasets.py`、`backend/app/infrastructure/sql_dataset_service.py`、`frontend/src/api/datasets.ts` 和 `frontend/src/features/analytics/AnalyticsWorkbench.tsx`。
