# TMS v0.6 前端架构基线：统一清洗结果 + React + Ant Design + ECharts

> 版本：v0.6  
> 状态：免费基础版前端技术栈冻结  
> 目标：在不引入付费前端控件的前提下，支撑 CP/FT 工程数据分析、数据治理和日常内部使用。

---

## 1. 最终选型

```text
React + TypeScript + Vite
│
├─ Ant Design
│  ├─ Layout / Menu / Form / Modal / Drawer / Upload
│  └─ Table：普通列表 + CP/FT 工程明细基础版
│
├─ Apache ECharts
│  └─ Yield / Bin / Distribution / Scatter / SPC / Wafer Map
│
├─ TanStack Query
│  └─ Server State / Query Cache / Request lifecycle
│
├─ Zustand
│  └─ 少量跨页面 UI 状态
│
└─ React Router
   └─ SPA Router
```

第一阶段明确不引入：

```text
Angular
Vue
Next.js
Redux
Wijmo / ComponentOne（付费，作为进阶版）
其他付费 DataGrid
```

---

## 2. v0.5 的核心前端原则

TMS 的前端不是普通 CRUD 后台，但第一阶段没有必要为了“专业 Grid”提前引入商业授权。

组件职责冻结为：

```text
Ant Design     = Application UI + Free Data Table
Apache ECharts = Analytics Visualization
```

对于大数据场景，优先通过后端解决：

```text
SQL Server
  ↓ WHERE / GROUP BY / ORDER BY / Pagination
FastAPI
  ↓ page data / aggregated data
React + Ant Design Table / ECharts
```

禁止依赖浏览器一次性加载数十万或数百万行后再本地筛选。

---

## 3. 免费基础版 vs 进阶版

### 3.1 v0.5 免费基础版（当前正式基线）

```text
React
+ TypeScript
+ Vite
+ Ant Design
+ Apache ECharts
+ TanStack Query
+ Zustand
```

适合当前目标：

- 内部 TMS；
- CP/FT 文件导入；
- Lot/Wafer/Unit 查询；
- Yield/Bin/CPK/PAT/SPC；
- Wafer Map；
- 参数分布、散点、相关性；
- CP/FT 明细分页浏览；
- 数据治理页面；
- Excel/CSV 导出由后端或通用导出模块完成。

### 3.2 进阶版（未来可选）

当出现以下明确需求时，再评估 Wijmo/ComponentOne：

- 工程师要求 Excel 式大范围复制/粘贴；
- 大量冻结列、列分组、复杂单元格编辑；
- Wafer Summary/参数矩阵需要成熟的转置 Grid；
- 需要高级 Pivot/OLAP 交互；
- 前端表格交互成为主要生产力工具；
- 自研这些能力的成本明显高于商业授权。

**原则：基础版代码不能硬依赖 Wijmo API。**

后续如果升级，优先替换统一 `EngineeringTable` 组件内部实现，而不是重写业务页面。

---

## 4. 页面信息架构

建议一级导航：

```text
Dashboard

数据接入
├─ 文件导入
├─ 导入任务
└─ 数据质量

CP 分析
├─ Lot / Wafer 浏览
├─ Wafer Map
├─ CP Die 明细
├─ Bin 分析
└─ 参数分析

FT 分析
├─ Lot 浏览
├─ FT Unit 明细
├─ Soft/Hard Bin
├─ Fail Item
└─ 参数分析

统计分析
├─ Yield Trend
├─ Distribution / BoxPlot
├─ Scatter / Correlation
├─ CPK
├─ PAT / SBL
├─ SPC
└─ Margin / Out-of-Spec

主数据
├─ Product
├─ Supplier
├─ Test Program
├─ Spec
└─ Bin Definition

追溯
└─ CP → Assembly → FT

系统管理
├─ User / Role
├─ Parser Profile
└─ Audit / Job
```

第一阶段按 Feature Flag/权限逐步开放，不要求一次全部完成。

---

## 5. 页面与组件映射

| 页面/区域 | v0.5 首选组件 | 说明 |
|---|---|---|
| 主框架 / Menu / Breadcrumb | Ant Design | 企业后台壳层 |
| 文件上传 | Ant Design Upload | 配合 Processing Job |
| 筛选条件 | Ant Design Form/Select/DatePicker | 通用 |
| Product/Spec/Bin 管理 | Ant Design Table | 操作型列表 |
| Lot/Wafer 导航列表 | Ant Design Table | 普通查询 |
| CP Die 明细 | Ant Design Table | 服务端分页、动态列、固定列 |
| FT Unit 明细 | Ant Design Table | 服务端分页、动态列 |
| Measurement 明细 | Ant Design Table | 默认 Long Format 分页展示 |
| Wafer Summary | Ant Design Table | 固定前几列 + 横向滚动 |
| 多参数矩阵 | Ant Design Table | 动态 Column Metadata |
| PAT/SPC/超限明细 | Ant Design Table | 服务端查询 |
| Yield/Bin | ECharts | 图表 |
| Histogram/Normal | ECharts | 分布 |
| BoxPlot | ECharts | 参数对比 |
| Scatter/Correlation | ECharts | 参数关系 |
| SPC | ECharts | I-MR |
| Wafer Map | ECharts custom series 第一阶段 | 后续可升级 Canvas/WebGL |
| Die Detail | Ant Design Drawer + Table/Descriptions | 点击 Die 钻取 |

---

## 6. 推荐前端目录

```text
frontend/
├─ src/
│  ├─ app/
│  │  ├─ App.tsx
│  │  ├─ router.tsx
│  │  ├─ providers.tsx
│  │  └─ theme.ts
│  │
│  ├─ pages/
│  │  ├─ dashboard/
│  │  ├─ ingestion/
│  │  ├─ cp/
│  │  ├─ ft/
│  │  ├─ analytics/
│  │  ├─ mdm/
│  │  ├─ governance/
│  │  └─ traceability/
│  │
│  ├─ features/
│  │  ├─ lot-browser/
│  │  ├─ wafer-analysis/
│  │  ├─ parameter-analysis/
│  │  ├─ yield-analysis/
│  │  ├─ cpk/
│  │  ├─ pat/
│  │  └─ spc/
│  │
│  ├─ components/
│  │  ├─ table/
│  │  │  ├─ EngineeringTable.tsx
│  │  │  ├─ MeasurementTable.tsx
│  │  │  ├─ WaferSummaryTable.tsx
│  │  │  └─ columnBuilders.ts
│  │  ├─ charts/
│  │  │  ├─ YieldChart.tsx
│  │  │  ├─ ParetoChart.tsx
│  │  │  ├─ DistributionChart.tsx
│  │  │  ├─ BoxPlotChart.tsx
│  │  │  ├─ ScatterChart.tsx
│  │  │  └─ SpcChart.tsx
│  │  ├─ wafer-map/
│  │  │  ├─ WaferMap.tsx
│  │  │  ├─ WaferLegend.tsx
│  │  │  └─ DieDetailDrawer.tsx
│  │  └─ common/
│  │
│  ├─ api/
│  │  ├─ client.ts
│  │  ├─ ingestion.ts
│  │  ├─ runs.ts
│  │  ├─ units.ts
│  │  ├─ measurements.ts
│  │  └─ analytics.ts
│  │
│  ├─ stores/
│  │  ├─ workspaceStore.ts
│  │  └─ preferenceStore.ts
│  │
│  ├─ types/
│  └─ utils/
└─ tests/
```

### 目录原则

- `pages`：路由级组合。
- `features`：业务场景。
- `components/table`：统一封装 Ant Design Table，业务页面不重复实现分页/排序/动态列逻辑。
- `components/charts`：统一封装 ECharts。
- `api`：只负责 HTTP DTO，不管理 UI 状态。

---

## 7. EngineeringTable 封装原则

虽然使用免费 Ant Design Table，也不要让业务页面直接各写一套表格。

统一封装：

```text
<EngineeringTable />
```

负责：

```text
Loading
Empty State
Server Pagination
Server Sort
Server Filter
Column Metadata
Fixed Columns
Horizontal Scroll
Column Visibility
Row Selection
Number Formatting
Spec/Bin Status Rendering
Export Trigger
Error Handling
```

业务页面只传：

```text
columns
queryKey
filters
rowKey
pageSize
exportName
```

这样以后若升级 Wijmo，只替换 `EngineeringTable` 内部实现和少量 Adapter。

---

## 8. 大表策略

### 8.1 后端优先

所有 CP/FT 明细默认：

```text
Server-side Pagination
Server-side Sort
Server-side Filter
```

建议：

```text
Default pageSize = 200
可选 = 100 / 200 / 500 / 1000
```

具体上限通过实际浏览器性能测试决定。

### 8.2 禁止模式

```text
SELECT 全表
→ API 返回 300,000 rows
→ Browser AntD Table
→ 前端筛选
```

禁止用于生产页面。

### 8.3 动态参数列

CP/FT 参数随产品和 Test Program 变化，不能把参数字段写死。

推荐：

```text
GET /api/v1/runs/{run_id}/grid-schema
```

返回：

```json
{
  "columns": [
    {"key":"vth_t2","label":"T2 VTH","unit":"V","type":"number"},
    {"key":"bvdss_t6","label":"T6 BVDSS","unit":"V","type":"number"}
  ]
}
```

前端由 `columnBuilders.ts` 根据 Metadata 生成 Ant Design Columns。

---

## 9. Wide View 与 Long View

数据库 `measurement` 保持 Long Format；前端根据页面需求提供两种视图。

### Long View

```text
Unit | Test No | Parameter | Value | Unit | Status | Spec | Evaluation
```

用于：

- Measurement 明细；
- 数据追溯；
- DQ；
- 单颗 Die/Unit 详情。

### Wide View

```text
Unit | X | Y | Bin | VTH | BVDSS | IDSS | RDSON | ...
```

用于：

- CP Die 工程明细；
- FT Unit 横向参数浏览。

Wide View 推荐由后端按选定参数动态 Pivot/组装后返回，而不是浏览器把海量 Long 数据自行 Pivot。

---

## 10. Excel/CSV 导出策略

v0.5 不依赖商业 Grid 自带 Excel 导出。

默认策略：

```text
当前页快速导出
→ POST Export Job（CURRENT_PAGE scope）
→ 后端登记授权与审计后生成文件

完整筛选结果导出
→ FastAPI 创建 Export Job
→ SQL Server 查询/流式生成文件
→ 用户下载
```

大结果集必须走后端 Export Job，避免浏览器内存爆炸。

导出必须携带：

```text
筛选条件
数据版本 / processing_run
Spec/Evaluation context（若导出评价结果）
导出时间
导出用户
```

---

## 11. ECharts 封装原则

按分析语义封装：

```text
YieldChart
BinParetoChart
DistributionChart
BoxPlotChart
ScatterChart
CorrelationHeatmap
SpcChart
WaferMap
```

图表组件负责：

- 展示；
- Tooltip；
- Brush/Zoom；
- Selection；
- Drill-down；
- 图片导出。

统计口径放后端/分析服务，不在多个 React 页面重复实现 CPK/PAT/SPC 算法。

---

## 12. Wafer Map

第一阶段采用 ECharts custom series / heatmap。

```ts
interface WaferDiePoint {
  unitId: number;
  x: number;
  y: number;
  bin?: string | null;
  result?: 'PASS' | 'FAIL' | null;
  value?: number | null;
  status?: string | null;
}
```

交互：

```text
Hover Die
→ X/Y/Bin/Parameter Value

Click Die
→ Ant Design Drawer
→ Unit Summary
→ MeasurementTable

Parameter Selector
→ Parameter Heatmap

BIN Selector
→ BIN Map
```

只有在 Multi-Wafer Overlay、复杂动画或超大 Die 数量导致性能不足时，再升级 Canvas/WebGL，外部业务接口不变。

---

## 13. Server State 与 Client State

### TanStack Query

管理服务器数据：

```text
Lot list
Wafer list
Run detail
Yield/Bin
Wafer Map
Parameter stats
CPK/PAT/SPC
Table page data
Job status
DQ issues
```

### Zustand

只管理少量跨页面客户端状态：

```text
当前 Product/Lot/Wafer 工作区
用户表格偏好
界面显示偏好
```

不要把 API 返回的大数据集合复制到 Zustand。

---

## 14. API 约定

统一前缀：

```text
/api/v1
```

典型接口：

```text
POST /api/v1/imports
GET  /api/v1/imports/{id}

GET  /api/v1/processing-runs
GET  /api/v1/processing-runs/{processing_run_id}

GET  /api/v1/dataset-versions/{dataset_version_id}
GET  /api/v1/dataset-versions/{dataset_version_id}/units
GET  /api/v1/dataset-versions/{dataset_version_id}/wafer-map
GET  /api/v1/dataset-versions/{dataset_version_id}/grid-schema

GET  /api/v1/analytics/yield
GET  /api/v1/analytics/bin-pareto
GET  /api/v1/analytics/parameter-distribution
GET  /api/v1/analytics/cpk
GET  /api/v1/analytics/correlation
GET  /api/v1/analytics/pat
GET  /api/v1/analytics/spc

POST /api/v1/exports
GET  /api/v1/export-jobs/{export_job_id}
```

分页响应：

```json
{
  "items": [],
  "page": 1,
  "pageSize": 200,
  "total": 7356
}
```

---

## 15. 前端性能基线

### Table

- 默认服务端分页。
- 固定识别列：Unit/X/Y/Bin/Result 等。
- 参数列按用户当前选择动态返回，避免一次返回全部参数。
- 需要横向宽表时启用 `scroll.x`、固定关键列。
- 可评估 Ant Design Table 的虚拟滚动，但不能以虚拟滚动替代服务器分页。
- Measurement 全量不得进入浏览器缓存。

### Chart

- Scatter 超大点集由后端采样/聚合，明确原始口径。
- Correlation 后端计算矩阵。
- Wafer Map 单片可一次返回。
- Multi-Wafer 由后端按需求聚合。

### Bundle

- 分析页面路由懒加载。
- ECharts 按需加载能力优先。
- 不提前引入商业 Grid Bundle。

---

## 16. 主题与设计规范

以 Ant Design Token 作为唯一 UI Theme 基线：

```text
字体
字号
间距
背景
边框
状态色
PASS/FAIL/Warning
```

ECharts 从同一套 TMS Theme Token 派生颜色。

业务颜色集中管理：

```text
PASS
FAIL
WARNING
NOT_TESTED
OVER_RANGE
```

Bin 颜色、名称和 Failure Mode 必须来自 API/`mdm.bin_definition`，禁止写死供应商 Bin 含义。

---

## 17. 第一阶段页面原型

第一阶段只做 5 个关键页面：

### 17.1 文件导入

```text
Ant Design Upload
→ Parser识别
→ Job Progress
→ Data Quality Summary
```

### 17.2 CP Lot/Wafer 浏览

```text
Product / Lot Filter
→ Ant Design Table
→ Wafer List
→ Yield / Bin Summary
```

### 17.3 Wafer Analysis

```text
ECharts Wafer Map
+ Parameter Selector
+ Bin Selector
+ Die Detail Drawer
```

### 17.4 CP Detail Table

```text
EngineeringTable (Ant Design Table)
→ X/Y/Bin + Selected Parameters
→ Server Filter / Sort / Pagination
→ Fixed Columns / Export
```

### 17.5 FT Detail Table

```text
EngineeringTable (Ant Design Table)
→ Unit / Soft Bin / Hard Bin / Result / Fail Item / Selected Parameters
```

先跑通这 5 个页面，再增加 CPK、PAT、SPC。

---

## 18. 数据治理页面

v0.5 继续保留 v0.4 数据治理：

```text
数据接入
├─ Import Batch
├─ Source File / Receipt
├─ Processing Job / Run
└─ Data Quality Issues

规则管理
├─ Spec Set / Binding
├─ Bin Mapping Set / Binding
└─ Scope Priority

系统治理
├─ Parser Versions
├─ Audit Log
└─ Migration / Application Version
```

全部基础版均使用 Ant Design Table/Form/Drawer。

---

## 19. 测试策略

```text
Unit Test
├─ DTO mapper
├─ AntD column builder
├─ pagination/filter mapper
├─ spec/bin display logic
└─ utility

Component Test
├─ EngineeringTable
├─ WaferMap
├─ DieDetailDrawer
└─ FilterBar

E2E
├─ Upload CP
├─ Open Lot/Wafer
├─ Draw Wafer Map
├─ Click Die
├─ View Measurements
└─ Export filtered result
```

重点测试数据口径和钻取链路，而不是只测页面能否打开。

---

## 20. Codex / AI 开发约束

建议放入 `AGENTS.md`：

1. 所有普通列表和第一阶段工程明细统一使用 Ant Design Table / `EngineeringTable`。
2. 禁止自行增加第二套免费/付费 Grid，除非架构评审通过。
3. Wijmo/C1 只属于可选进阶版，基础版代码不得依赖其包或 API。
4. 新分析图优先复用 ECharts wrapper。
5. 禁止页面直接访问数据库。
6. 禁止页面判断供应商原始字段。
7. 禁止一次请求无上限 Measurement。
8. CPK/PAT/SPC/Spec Resolution 等统计与规则判断优先后端实现。
9. Vendor 差异必须放 Parser/Mapping，不进入 UI 条件分支。
10. Wide Table 的动态参数列必须由 Metadata/API 驱动。
11. 大结果集导出走后端 Export Job。
12. 新增依赖前先确认 React/AntD/ECharts 是否已覆盖需求。

---

## 21. 技术栈冻结结论

### 当前正式基础版

```text
React + TypeScript + Vite
Ant Design + Ant Design Table
Apache ECharts
TanStack Query
Zustand
React Router
```

### 可选进阶版

```text
Wijmo / ComponentOne FlexGrid
```

只在明确获得授权且专业 Grid 需求达到收益门槛后启用。

### 不变原则

```text
业务页面
    ↓
EngineeringTable abstraction
    ↓
当前：Ant Design Table
未来可选：Wijmo Adapter
```

因此未来升级专业 Grid 不要求重构 Canonical Model、API、业务页面和分析逻辑。

---

## 22. v0.5 冻结结论

v0.5 的核心目标不是追求最强前端 Grid，而是：

> **先用完全免费的成熟技术栈把 TMS 的数据模型、Parser、分析逻辑、Wafer Map 和业务闭环跑通。**

如果未来工程人员明确反馈 Ant Design Table 已成为效率瓶颈，再用实际需求和 ROI 决定是否升级 Wijmo/C1。

## 23. v0.6 产品信息架构覆盖

v0.6 的一级导航按用户业务流程组织：

```text
工作台
数据接入与任务
已发布数据集
CP 分析
FT 分析
规则中心
数据质量与审计
导出与报告
用户与权限（管理员）
```

厂家与 Format Profile 是任务元数据和治理维度，不为每个厂家复制一套用户分析页面。

## 24. Dataset Context Bar

所有分析页面顶部使用同一 Context Bar：Dataset Version、Stage、Supplier、Product、Lot/Test Batch、Processing/Cleaner Version、Spec/Bin/Evaluation Context、Filter Summary、更新时间。

Context Bar 改变后，表格、图表和导出请求必须共享同一个规范化筛选对象。前端不得把不同响应拼成没有一致数据版本的页面。

## 25. 图表能力分组

从 VDMOS HTML 借鉴图表覆盖面，但按业务分组而不是平铺大量 Tab：

- Overview：Yield、Bin、Pareto、风险摘要；
- Detail：CP Die、FT Unit、Measurement；
- Parameter：BoxPlot、Distribution、Scatter、Correlation、Cpk；
- CP Spatial：Wafer Map、Heatmap、Overlay、Stack、Zone；
- Quality：PAT、SBL、SPC、Margin、Out-of-Spec；
- Delivery：Wafer Summary、Saved Analysis、Report、Export。

首屏不自动计算全部图表。Lot/Wafer/Parameter 默认全部选择，并支持全选、单选、多选；用户点击绘图后才发起高成本分析请求。

## 26. 统计与清洗边界

禁止前端：

1. 解析生产原始文件形成第二套清洗结果；
2. 硬编码 `BIN=1` 为 PASS；
3. 在图表层静默执行 IQR 删除或单位转换；
4. 找不到 Spec 时拼接全部产品规格或取第一份；
5. 在 Local Storage 保存正式 Dataset/Analysis 数据；
6. 自行计算权威 Yield/Cpk/PAT/SBL/SPC。

前端可以保存主题、列宽和非业务显示偏好；正式 Saved Analysis 必须写后端并固定 Dataset Version。

## 27. v0.6 API 补充

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me

POST /api/v1/input-sets
POST /api/v1/processing-jobs
GET  /api/v1/processing-jobs/{processing_job_id}
POST /api/v1/processing-runs/{processing_run_id}/publish

GET  /api/v1/datasets
GET  /api/v1/dataset-versions/{dataset_version_id}

POST /api/v1/evaluation-runs
GET  /api/v1/evaluation-runs/{evaluation_run_id}

POST /api/v1/saved-analyses
GET  /api/v1/saved-analyses/{saved_analysis_id}

POST /api/v1/exports
GET  /api/v1/export-jobs/{export_job_id}
GET  /api/v1/export-artifacts/{artifact_id}/download-token
```

每个数据接口执行对象级授权并返回 `dataset_version_id`、`filter_summary`；统计接口额外返回 `evaluation_run_id` 和 `rule_version`。
