# TMS VDMOS 参考算法审计

- 审计日期：2026-08-30
- TMS 仓库：`F:\CP-FT数据分析`
- TMS 代码基线：`f589687`（`codex/auth-rbac-frontend`）
- 参考实现：`VDMOS_Tool_v8.9.html`
- 对照实现：`F:\cp_data_ansys` 当前 CP Cockpit
- 审计方式：只读源码、数据合同和既有 Golden 测试检查
- 本文边界：只给出可迁移/禁止迁移判断、算法风险、TMS 规则门禁和 Golden 向量；不修改代码、数据库、开发计划或发布状态

## 1. 结论先行

VDMOS v8.9 可以作为**最终用户交互和图表表现参考**，不能作为 TMS 的原始数据解析器、规格库、Bin 规则库或第二套统计引擎。

本轮审计的核心结论是：

1. **可以直接借鉴的主要是展示逻辑**：多图卡片、图例显隐、手动轴范围、Lot/Wafer 分组配色、悬浮信息、轻量/详看模式、图层开关和导出交互。
2. **不能直接迁移的主要是计算逻辑**：VDMOS 在浏览器中混合了解析、硬编码 Spec、Pass Bin=1、异常值删除、随机抽样、统计计算和业务结论，多个算法还存在确定性错误。
3. **当前 CP Cockpit 是更好的 CP 图表基线**：它只消费标准 cleaned/yield/spec，按 `Lot_ID + Wafer_ID` 识别物理晶圆，Mapping 对缺 Spec 和反向 Spec 失败关闭，并保留坐标 0、负坐标和复测计数。
4. **当前 CP Cockpit 也不是可直接复制的权威统计内核**：BoxPlot、Scatter、Zone、Overlay 和 Cpk 仍有无统一测试、规格轴裁剪、残缺 Map 误分区、同片号跨 Lot 混合，以及 Cpk 样本门槛不一致等问题。
5. **TMS 必须坚持 Backend Authority**：浏览器只负责选择和展示；分位数、直方图、相关性、Cpk/Ppk、空间聚合、PAT 和 SPC 由后端或 Worker 按版本化规则计算，并返回可对账的输入数、排除数、采样数和规则上下文。

一句话判断：**迁交互，不迁隐含语义；迁图层，不迁浏览器公式；以 TMS Canonical、Lot 级 Spec、版本化 Bin/规则和 Golden 为唯一事实。**

## 2. 证据基线与 SHA-256

### 2.1 参考文件

| 文件 | SHA-256 | 说明 |
|---|---|---|
| `F:\CP-FT数据分析\历史项目-参考用\fjd项目\VDMOS_Tool_v8.9.html` | `23C899964F9C86D312B3BD686AA2E5F7478644F791EAB05D4B369DAF1BE4C0A3` | 本轮 VDMOS 主审计对象，604,253 bytes |
| `F:\CP-FT数据分析\历史项目-参考用\fjd项目\X1\X1\tool_files\VDMOS_Tool_v8.9.html` | `23C899964F9C86D312B3BD686AA2E5F7478644F791EAB05D4B369DAF1BE4C0A3` | 与主文件逐字节一致的副本 |
| `F:\cp_data_ansys\frontend\cp_dashboard_app.py` | `297427EF0AF1B15920B3441337F34C231278234E505149BE95F027EE56F29FB8` | 当前 Streamlit CP Cockpit 主实现 |
| `F:\cp_data_ansys\frontend\charts\wafer_mapping.py` | `4FFE40D627284F878C129688BCBDED10188FB67A85EC5D14673913509FFA9A91` | 当前 Bin/参数 Mapping 实现 |
| `F:\cp_data_ansys\frontend\tests\test_wafer_mapping.py` | `B33FEC94CE7A33D6053519BD8B71E0157F3554B1AA46E61CA7A0D0AB02322C0E` | 既有 Mapping Golden 种子 |
| `F:\cp_data_ansys\docs\data-contracts.md` | `21B961231ECAC71AEEAA6E94CAB00E068A0187B411C445B2952725C72BE0E3D9` | CP 标准 cleaned/yield/spec 与 Pass Bin 合同 |

### 2.2 关键源码位置

| 能力 | VDMOS v8.9 | 当前 CP Cockpit |
|---|---|---|
| 内置 Spec / Bin | `VDMOS_Tool_v8.9.html:150-159` | `docs/data-contracts.md:35-71,144-151` |
| 参数识别/原始解析 | `VDMOS_Tool_v8.9.html:666-668,1212-1225,1548-1573` | `cp_dashboard_app.py:4-10,50-71,691-758` |
| BoxPlot | `VDMOS_Tool_v8.9.html:1813-1861,1996-2084,4659-4930` | `cp_dashboard_app.py:1092-1189` |
| Wafer Scatter | `VDMOS_Tool_v8.9.html:2429-2587,2649-2802` | `cp_dashboard_app.py:1192-1275,1719-1732` |
| Histogram / Normal | `VDMOS_Tool_v8.9.html:3951-4125,6359-6595` | 当前 Cockpit 无对应实现；`frontend/README.md:38-48` |
| Correlation | `VDMOS_Tool_v8.9.html:4129-4195` | 当前 Cockpit 无 pairwise correlation；现“散点相关”是 Wafer 分布散点 |
| Cpk | `VDMOS_Tool_v8.9.html:1847-1861,4222-4234,6534-6588` | `cp_dashboard_app.py:1406-1438` |
| Wafer Map / Heatmap | `VDMOS_Tool_v8.9.html:3163-3947` | `wafer_mapping.py:80-178,242-455` |
| Zone | `VDMOS_Tool_v8.9.html:4238-4294` | `cp_dashboard_app.py:815-842,1278-1332,1833-1843` |
| 多 Wafer 复合失效 | `VDMOS_Tool_v8.9.html:4298-4406` | 当前 Cockpit 无按物理 Wafer 覆盖率聚合的等价实现 |
| Parameter Overlay | `VDMOS_Tool_v8.9.html:5311-5435` | `cp_dashboard_app.py:1374-1403,1845-1852` |
| PAT | `VDMOS_Tool_v8.9.html:6025-6091` | 当前 Cockpit 无 PAT 实现 |
| SPC I-MR | `VDMOS_Tool_v8.9.html:6195-6356` | 当前 Cockpit 无 SPC 实现 |

### 2.3 权威边界

`F:\cp_data_ansys\frontend\cp_dashboard_app.py:4-10` 和
`F:\cp_data_ansys\docs\data-contracts.md:35-71,144-151` 已明确：

- 图表层只读取标准 cleaned/yield/spec；
- 图表层不重新解析厂商原始文件；
- 图表层不修改测试值、Bin、单位或规格；
- 物理 Wafer 身份是 `Lot_ID + Wafer_ID`；
- Pass Bin 必须显式来自已批准合同，不能在公共层写死为 1；
- 多 Lot Spec 不得任取第一份，必须按 Lot 绑定。

本文与归档的 `docs/archive/2026-08-24_to_2026-09-03_delivery-records/development/TMS_Analytics_Closure_Development_Plan_v1.3_2026-08-30.md` 互补：开发计划定义当时的目标能力和工作包，本文冻结 VDMOS 参考算法中哪些内容可借鉴、哪些内容必须拒绝。

## 3. 共性红线：以下行为全部禁止迁入 TMS

### 3.1 禁止把缺失或非法 Bin 变成良品

VDMOS 多处使用：

```text
parseInt(rawBin) || 1
r._Bin || 1
```

这会把 Bin=0、空值、非法文本和部分解析失败变成 Pass Bin 1。TMS 必须保留 `UNKNOWN`/缺失状态或失败关闭，不得伪造良品。

### 3.2 禁止硬编码 Pass Bin=1、产品 Spec 和 Bin 含义

VDMOS 在 HTML 中内置产品规格和 VDMOS 专用 Bin 含义，并在 Yield、Scatter、Map、Zone、Overlay、Stack 和报表中反复写死 `_Bin === 1`。这些内容只适用于特定历史产品，不能进入 TMS 公共分析层。

### 3.3 禁止用通用阈值静默删除 `9999/-9999`

VDMOS 在部分统计中使用 `Math.abs(v) < 9999`，但其他图表又不使用同一规则。`9999` 是否哨兵值必须来自具体 Cleaner/参数合同；合法测量值不得被图表层静默删除。

### 3.4 禁止静默 IQR 删异常后再计算能力

VDMOS Histogram/Normal 会先删 IQR 外点，再计算均值、标准差、正态拟合、Cp/Cpk。显示过滤可以是显式可逆视图，但不能改变默认统计总体，更不能用被删后的总体给出放行或能力结论。

### 3.5 禁止随机采样改变每次结果

VDMOS 自定义 Scatter 和 3D Scatter 使用 `Math.random()`。TMS 显示采样必须确定性、可复现，并强制保留超规格点、PAT 命中、SPC Rule Hit 和空间异常证据；权威统计不得在显示样本上计算。

### 3.6 禁止把 Spec Limit、Control Limit 和 PAT Limit 混为一体

VDMOS Scatter 在无 Spec 时回退 `mean ± 3σ`，并继续标成 UCL/LCL；有 Spec 时又把 LSL/USL 当作 UCL/LCL。TMS 必须分别返回：

- 工程规格：LSL/USL；
- 过程控制限：CL/UCL/LCL；
- PAT 动态限：PAT_L/PAT_U；
- 显示轴范围：Display Min/Max。

四者不得互相回退或改名。

### 3.7 禁止从空间图直接写根因

VDMOS Zone 标题直接把 Edge 失效归因为蚀刻/薄膜均匀性。空间图只能输出形态、证据点和候选调查方向；没有 MES、FDC、WAT、缺陷、FA/FT 等证据，不得宣称真实根因。

## 4. 逐项算法审计与迁移判断

## 4.1 BoxPlot

### 输入字段

- 必需：Dataset Version、`Lot_ID`、`Wafer_ID`、参数身份、参数值；
- 可选：单位、LSL、USL、测试条件、Bin、Retest/Seq；
- 参数身份不得只用显示名称，至少应包含 Stage、参数代码/名称、Occurrence/Step、单位和测试条件。

### VDMOS 实际算法

`VDMOS_Tool_v8.9.html:1813-1861,4922-4930`：

- 先排序；
- sample standard deviation，分母 `n-1`；
- Q1/Median/Q3 直接取 `floor(n*0.25/0.5/0.75)` 位置；
- 少于 5 个有效值整组跳过；
- IQR 外点阈值为 `Q1-1.5IQR`、`Q3+1.5IQR`；
- “须线”直接使用阈值与 min/max 的裁剪值，不保证是实际观测值；
- 部分路径静默排除 `abs(value) >= 9999`。

### 已确认风险

1. `s.lw || s.min`、`s.uw || s.max` 在须线恰为 0 时错误回退到极值，见 `VDMOS_Tool_v8.9.html:2070,4685`。
2. 按 `_Wafer` 分组，不带 Lot，可能把不同 Lot 的相同片号合成一个箱，见 `VDMOS_Tool_v8.9.html:2012,2037`。
3. VDMOS 四分位算法与 NumPy/Pandas 默认线性分位数不一致，小样本差异明显。
4. `n<5`、哨兵值、外点显隐均没有业务规则版本。
5. 多产品时总体统计取首行 Product 的 Spec，可能套错规格。

### 当前 CP Cockpit

`cp_dashboard_app.py:1092-1189` 已做到：

- 按 `Lot_ID + Wafer_ID` 分箱；
- `np.percentile(..., 25/50/75)` 线性分位数；
- 须线取阈值内实际观测最小/最大值；
- `go.Box` 只展示箱体，`boxpoints=False`；
- Lot 分色，Hover 显示 N、Min、Q1、Median、Q3、Max。

仍需修正/门禁：

- 没有直接 BoxPlot Golden 覆盖；
- 双侧 Spec 时 Y 轴按 Spec 范围自动裁剪，极端超限值可能不在视口内；
- Spec 方向异常没有在 BoxPlot 入口统一拒绝；
- `prepare_wafer_axis` 缺 Lot/Wafer 时会构造占位身份，TMS 不应复制该降级。

### 可迁移

- 逐参数卡片；
- Lot/Wafer 颜色和顺序；
- 中位数 Hover；
- 外点图层显隐；
- 手动 Y 轴范围；
- 规格参考线；
- 大参数集懒加载。

### 禁止迁移

- VDMOS 的 floor-index 分位数；
- 通用 `9999` 哨兵；
- 仅 `_Wafer` 分组；
- 0 值 `||` 回退；
- 隐藏外点后不显示隐藏数；
- 只按 Spec 轴范围裁掉数据却不告警。

### TMS 计算位置

- 后端同步统计服务计算 Q1/Median/Q3、IQR、实际须线、外点数；
- 前端只决定外点是否显示、轴范围和 Hover；
- 响应返回 quantile method、included N、missing N、outlier display count 和规则版本。

## 4.2 Histogram / Normal

### 输入字段

- Dataset/Filter Context；
- 参数身份和值；
- 可选 LSL/USL/Target；
- 明确的 bin request 或服务端 bin policy；
- 是否显示拟合线，不等于是否删除数据。

### VDMOS 实际算法

`VDMOS_Tool_v8.9.html:3989-4125,6378-6595`：

- 先按 floor-index Q1/Q3 用 1.5IQR 删除外点；
- 对剩余值计算 sample mean/std；
- bin 数为 `ceil(sqrt(n))`，上限 30 或 40；
- 按频数缩放正态 PDF；
- 画 LSL/USL、Target、Mean、Mean±3σ；
- 在 Normal 侧栏计算 Skew、Kurt、Cp、Cpk；
- 默认最少 5 或 10 点。

### 已确认风险

1. 默认删外点后再算能力，违反 No Silent Filter。
2. `VDMOS_Tool_v8.9.html:6415-6418` 按 `xMin` 分箱，但 `:6461-6468` 从 `sorted[0]` 开始画柱；当 Spec 或 ±3σ 扩展左边界时，柱位置与计数区间错位。
3. `std=0` 时 PDF、Skew、Kurt 存在除零/非有限风险。
4. 没有正态性检验，却把正态拟合曲线和 Cpk 组合成“正态分布”结论。
5. 偏移 15%、Cp/Cpk 1.0/1.33 是写死阈值，不是版本化业务规则。
6. 一侧 Spec 的 Dist 参考线判断只检查 `specLo`，上限单侧显示不完整。
7. 拟合曲线可能造成“数据服从正态”的视觉误读。

### 当前 CP Cockpit

当前用户可见 Cockpit 没有 Histogram/Normal。仓库内 `SummaryStats.test_normality()` 提供 Shapiro-Wilk 和 Anderson-Darling 草案，但没有发现对应直接测试，不能视为已验收能力。

### 可迁移

- 直方图和拟合线叠加；
- LSL/USL/Target/Mean/±3σ 图例开关；
- N、Mean、Std、Min、Max、Skew、Kurt 统计侧栏；
- 柱顶计数；
- Raw/显式 View Filter 的双视图交互。

### 禁止迁移

- 默认 IQR 删除；
- 在显示样本上算能力；
- 拟合线等同正态性结论；
- std=0 继续画 PDF；
- 写死偏移/Cpk 结论；
- Hist 计数区间和绘图坐标使用不同起点。

### TMS 计算位置

- 后端计算固定 bin edges、counts、统计量和可选 normality diagnostics；
- 拟合线只在有限且 `std>0` 时返回；
- Raw、Missing、Excluded 和 Display Sample 分开计数；
- 正态性结果必须显示样本量、方法、显著性水平和“不构成稳定过程证明”。

## 4.3 Scatter / Correlation

### 输入字段

Wafer 分布 Scatter：

- `Lot_ID`、`Wafer_ID`、参数值；
- 可选 Bin、LSL/USL、Seq；
- 确定性显示采样策略。

参数 X/Y Scatter 和 Correlation：

- 同一 Unit/Die 行上的 X 参数和 Y 参数；
- 参数完整身份与单位；
- pairwise 有效 N；
- Dataset/Filter Context；
- 可选 Lot/Wafer/Bin/测试条件分组。

### VDMOS Scatter 实际算法与风险

`VDMOS_Tool_v8.9.html:2429-2587,2649-2802`：

- 常规 Scatter 的 X 是导入后行序号，不是稳定的 Seq/Time；
- 无 Spec 时用 `mean±3std` 回退为 UCL/LCL，有 Spec 时又把 Spec 当 UCL/LCL；
- 自定义 X/Y Scatter 能保持同一行 X/Y 配对；
- 自定义 Scatter 使用 `Math.random()` 抽样；
- 多文件自动模式只画 `_Bin===1`，失效点被隐去；
- 只展示部分 Wafer/点，但没有完整 sampling summary。

### VDMOS Correlation 的确定性错误

`VDMOS_Tool_v8.9.html:4136-4156` 对每个参数**分别删除缺失值**，再按最短数组长度按下标配对。这会把不同 Die 的值错配，所得 Pearson r 不可信。

此外：

- 常量列和不足 5 点返回 0，而不是 NULL/Not Assessable；
- 只取前 15 个参数；
- 按固定步长抽样可能混叠周期信号；
- 不返回 pair N、置信信息或缺失模式；
- 缺少参数兼容性和单位门禁。

### 当前 CP Cockpit

`cp_dashboard_app.py:1192-1275,1719-1732` 中“散点相关”实际是**每参数对 Lot/Wafer 顺序的分布散点**，不是参数两两相关。它使用 `random_state=42` 和固定 jitter seed，结果可复现。

该页面符合已确认的 CP Cockpit 用户意图，应继续保留为默认 Scatter。Pairwise Correlation 应作为独立分析能力，不能替换现有 Wafer 分布散点。

当前弱点：

- 简单随机显示采样可能漏掉稀有超规格点；
- Y 轴按 Spec 范围自动裁剪可能隐藏极端点；
- 采样摘要未返回原始数、保留超限数和抽样 Hash。

### 可迁移

- X/Y 参数选择；
- 按 Lot/Wafer/文件或 Pass/Fail 分组配色；
- 手动轴范围；
- 相关矩阵发散色阶和单元格 r 值；
- Wafer 分布 Scatter 与参数 X/Y Scatter 分成两类清晰入口。

### 禁止迁移

- 独立删 NaN 后错位相关；
- 常量列返回 r=0；
- 随机抽样；
- 多文件时静默只保留 Pass；
- Spec 与 Control Limit 混用；
- 在采样点上计算权威 r；
- 把相关性描述为根因。

### TMS 计算位置

- 后端按同一 Unit/Die 行执行 `dropna([x,y])` 后计算 pairwise r 和 N；
- 常量列、N 不足、参数不兼容返回明确状态，不返回伪 0；
- 权威 r 基于全体 included rows；显示点确定性采样并保留超规格和异常证据；
- 前端只渲染矩阵、Scatter 和筛选交互。

## 4.4 Cpk / Ppk

### 输入字段

- Dataset/Filter/Rule Context；
- 参数完整身份和值；
- Lot 级 Spec Version、LSL/USL/Target；
- subgroup、时间顺序和 sigma estimator；
- minimum N；
- missing/retest/outlier policy；
- 单侧或双侧能力语义。

### VDMOS 实际算法

主统计 `VDMOS_Tool_v8.9.html:1847-1861`：

- sample std；
- 至少 5 个值；
- 双侧 `min((mean-LSL)/(3s),(USL-mean)/(3s))`；
- 单侧可返回一侧指数；
- 对“负值单边 USL”使用专用反向公式 `(mean-spec[1])/(3s)`；
- Spec 取首行 Product 对应内置/解析规格。

Normal 页又在 IQR 删除后重新算 Cpk，形成不同口径。

### 已确认风险

1. HTML 内置历史产品 Spec，不能成为 TMS Master Data。
2. “负值单边 USL”特制公式混淆 LSL/USL 方向，只能留在有证据的产品 Adapter/Rule 中，不能泛化。
3. 一侧 Spec 在 Cpk 表中可能对 NULL 调用 `toExponential()`。
4. VDMOS 的最小样本门槛是 5，当前 Cockpit 是 2，仓库另外两套能力分析器是 30，口径冲突。
5. VDMOS 和当前 Cockpit 都用总体 sample std 标为 Cpk；没有 subgroup within-sigma 时，该值更接近 Ppk 口径，不能默认叫 Cpk。
6. 稳定性、正态性和过程分层均未形成门禁。

### 当前 CP Cockpit

`cp_dashboard_app.py:1406-1438`：

- sample std；
- `n>=2`；
- 仅双侧 Spec 计算 Cpk；
- 缺 Spec 或 std=0 返回 NaN；
- 同表给出低于 LSL、高于 USL 数。

仓库还存在：

- `cp_data_processor/analysis/summary_stats.py:118-171`；
- `cp_data_processor/analysis/capability_analyzer.py:101-161`。

两者要求 `n>=30` 并支持单侧。因此当前仓库至少存在 2/5/30 三种样本门槛，迁移前必须冻结唯一政策。

### 可迁移

- N、Mean、Std、LSL、USL、Cpl、Cpu、Cp、Cpk/Ppk、低超限数、高超限数表；
- 排序和分级颜色；
- 一侧/双侧能力的清晰标签；
- 从低能力参数下钻到原始 Unit/Die。

### 禁止迁移

- 内置 Spec；
- 首产品/首 Lot Spec；
- 删除外点后算默认能力；
- 把 overall sample std 无条件命名为 within Cpk；
- std=0 返回 0 或 Infinity；
- 反向 Spec 自动交换；
- 写死 1.0/1.33 放行结论；
- 未稳定/未正态却输出无警告的“合格”。

### TMS 计算位置

- 统一后端 capability kernel；
- Cpk 和 Ppk 使用不同且版本化的 sigma estimator；
- 先验证 Spec 方向和 Lot 绑定；
- 返回 `NOT_ASSESSABLE`、`INSUFFICIENT_N`、`ZERO_VARIANCE`、`SPEC_MISSING` 等明确状态；
- 阈值由 Rule Owner 配置，不写在 React/ECharts 中。

## 4.5 Heatmap / Overlay / Zone

### 输入字段

- `Lot_ID`、`Wafer_ID`、X、Y、Seq/Retest、Bin、Pass Bin；
- 可选参数值、单位、LSL/USL；
- Wafer Layout/方向/坐标版本；
- 物理 Wafer 覆盖集合；
- 同坐标复测选择规则。

### VDMOS Wafer Map / Heatmap 风险

VDMOS 多处使用：

```text
parseInt(Row/row/Y/...) || 0
parseInt(Col/col/X/...) || 0
filter(value !== 0)
```

后果：

- 合法坐标 0 被当作缺失；
- 小数坐标被截断；
- 多个原坐标可能碰撞；
- 同坐标复测通过 `grid[row][col]=record` 最后一条静默覆盖；
- 硬编码 Pass Bin=1；
- 参数色域通常按单片 min/max，跨 Wafer 同值颜色不一致；
- 选择“全部批次”时，同 `Wafer_ID` 可能跨 Lot 合并。

### VDMOS Zone 风险

`VDMOS_Tool_v8.9.html:4243-4255` 只按 Row 在全局最小/最大值之间三等分：

- 不是晶圆径向 Center/Mid/Edge；
- 坐标 0 被删除；
- 不按 `Lot_ID + Wafer_ID` 分别求中心；
- 不考虑晶圆版图、缺边、方向或缺失坐标；
- 直接附带工艺根因文字。

### VDMOS 多 Wafer Stack 风险

`VDMOS_Tool_v8.9.html:4360-4405` 用同坐标的 fail records / total records 表示“系统化失效率”。复测会重复加权，不等于“失效的物理 Wafer 数 / 有覆盖的物理 Wafer 数”。如果 Lot、产品版图或方向不同，叠加更无意义。

### 当前 CP Cockpit Mapping

`frontend/charts/wafer_mapping.py` 已提供更可靠基线：

- `Lot_ID + Wafer_ID` 复合身份；
- X/Y 数值化但保留 0 和负值；
- Bin 模式显式接收 `pass_bin`；
- 参数模式没有 LSL/USL 时拒绝；
- LSL>USL 时拒绝，不自动交换；
- 同坐标复测保留最高不良优先级，再按 Seq 选择；
- Hover 显示复测记录数；
- 全 Wafer 轻量视图和 1-25 片详看视图；
- 既有测试覆盖多 Lot 同片号、0/负坐标、单侧 Spec、反向 Spec 和复测优先级。

### 当前 CP Cockpit Zone / Overlay 弱点

Zone `cp_dashboard_app.py:815-842` 按每片 `Lot_ID + Wafer_ID` 的 X/Y 中位中心和观测最大半径归一化，明显优于 VDMOS Row 三等分，但仍有：

- 缺边或残缺 Map 会把观测最大半径误当真实边缘；
- 全坐标 0 会被填成 Center，Zone 页没有调用 Mapping 的 spatial gate；
- 0.33/0.66 阈值尚未版本化；
- 没有 Wafer Layout/方向证据。

Overlay `cp_dashboard_app.py:1374-1403`：

- 显式使用 Pass Bin；
- 仅保留 fail 点；
- 但按 `Wafer_ID` 而不是 `Lot_ID + Wafer_ID` 选择，跨 Lot 同片号可能混合；
- “全部 Wafer”是原始点叠加，不返回按物理 Wafer 覆盖率的密度；
- 显示抽样没有 prevalence denominator。

### 可迁移

- 全 Wafer 小图矩阵；
- 轻量总览/详看切换；
- Bin 状态、参数低超限/高超限离散图例；
- Hover 中的 Lot/Wafer/X/Y/Bin/参数/复测数；
- 参数热图、Fail Bin Overlay、跨 Wafer prevalence 图层；
- 图层显隐、缩放、固定全局色域和 PNG/受控导出；
- 侧栏显示覆盖 Wafer 数、判定 Die 数、Fail 数和重复坐标数。

### 禁止迁移

- `parseInt(...) || 0`；
- 删除坐标 0；
- 最后一条复测覆盖；
- `_Bin!==1`；
- Row 三等分 Zone；
- 每片独立 min/max 色域用于跨片比较；
- 用 record rate 冒充 wafer prevalence；
- 未验证版图/方向就跨产品叠加；
- 空间形态直接推断根因。

### TMS 计算位置

- 后端验证空间能力和 Layout/方向兼容；
- 后端按物理 Wafer 和复测规则生成 die-level 状态；
- 多 Wafer Overlay 返回 numerator wafers、denominator covered wafers 和 prevalence；
- 参数 Heatmap 返回固定的全局/Spec 色域；
- 大规模空间聚合和导出由 Worker 执行；
- 前端只绘制离散状态、连续色阶、图层和 Hover。

## 4.6 PAT

### 输入字段

- Dataset/Workspace Context；
- 参数完整身份和值；
- Lot/Wafer/参考群；
- Rule Code、k、quantile method、robust sigma estimator；
- Spec clamp 策略；
- Pass/Fail、retest、missing 和 minimum N 策略。

### VDMOS 实际算法

`VDMOS_Tool_v8.9.html:6036-6068`：

```text
PAT_L = Median - k * (IQR / 1.349)
PAT_U = Median + k * (IQR / 1.349)
```

- 默认 k=5；
- 至少 10 个值；
- Q1/Median/Q3 使用 floor-index；
- 所有数据 pooled，不分 Lot/Wafer/参考群；
- 只输出存在 outlier 的参数。

### 已确认风险

1. `parseFloat(input) || 5` 把 0、空和非法值改成 5，但负 k 会被接受，形成反向上下限。
2. `IQR` 被强制不小于 0.0001，常量或高精度参数得到虚假的波动。
3. 异常率分母使用整表记录数，而不是该参数有效 N。
4. 不分产品、Lot、Wafer、Pass/Fail、测试条件或参考群。
5. 不与工程 Spec 进行受控夹限。
6. 不返回无异常参数的 limit，不能完整审计。
7. 浏览器内一次性遍历不适合大目录精确 PAT。

### 当前 CP Cockpit

当前 CP Cockpit 没有 PAT。`python_cp/param_distribution_stats.py` 中有 IQR/1.34898 的 RobustStd 统计，但它不是已验收 PAT 引擎，不能据此宣称 PAT 已完成。

### 可迁移

- Sigma/k 输入交互；
- 参数级 Median、Robust Sigma、PAT_L/PAT_U、异常数、异常率和最大偏差表；
- 从参数行下钻异常 Die/Unit；
- CSV/受控报告导出。

### 禁止迁移

- 默认 pooled 全数据；
- floor-index 分位数；
- IQR 强制 0.0001；
- 总表行数作有效分母；
- 非法 k 回退 5；
- 负 k；
- 只显示有异常参数；
- 浏览器计算大规模正式 PAT。

### TMS 计算位置

- Quick 一次性原始目录计算放隔离 Workspace/Worker，不写入正式 Measurement；
- 正式 Dataset PAT 读取 Canonical Measurement，由后端/Worker 生成版本化分析结果；
- 输出必须记录规则、参考群、有效 N、缺失 N、上下限、命中证据、计算版本和 Context Hash；
- Rule Owner 未批准前只显示“候选规则/试算”，不得作为自动 Bin 或放行依据。

## 4.7 SPC I-MR

### 输入字段

- 参数身份和值；
- 明确的事件顺序字段：Seq、测试时间或业务时间；
- Lot/Wafer/机台/程序/测试条件分组；
- Phase I 基线窗口；
- Control Rule Version；
- moving range boundary/reset 规则。

### VDMOS 实际公式

`VDMOS_Tool_v8.9.html:6239-6248,6329-6347`：

```text
CL = mean
MR_i = abs(x_i - x_(i-1))
UCL/LCL = mean ± 2.66 * MRbar
MR_UCL = 3.267 * MRbar
```

这是 moving range=2 的常见 I-MR 常数，但正确公式不能弥补错误的顺序和分组。

### 已确认风险

1. 完全不读取 Seq/Time，按当前数组顺序计算 MR。
2. “全部 Wafer”图先按 Wafer 分组重排并最多只画前 10 片，控制限却基于全部原始记录顺序；点、MR 和控制限不是同一序列。
3. MR 会跨 Wafer/Lot 边界，制造不存在的跳变。
4. 所有 Lot/Wafer pooled 一套控制限。
5. 只判断单点越界，不支持 run/trend 等规则，也不返回规则版本。
6. 常量序列 `MRbar=0` 时 MR 图存在除零风险。
7. 自动 Y 轴用 `value*0.95/1.05`，对负值和零跨度不稳。
8. 3/5 的最小样本门槛没有业务来源。
9. 用当前待判数据自身重算控制限，可能稀释真正异常；没有冻结 Phase I baseline。

### 当前 CP Cockpit

当前 CP Cockpit 没有 SPC 实现，因此不存在可直接复用的当前权威计算内核。

### 可迁移

- I Chart、MR Chart 上下布局；
- CL/UCL/LCL/MR-UCL 显隐；
- Wafer/参数选择；
- 失控点高亮；
- 统计摘要和规则命中列表；
- 手动显示轴范围。

### 禁止迁移

- 数据库/数组自然顺序；
- 跨 Wafer MR；
- 前 10 片绘图但全体算限；
- 每次打开用当前全体数据重算 baseline；
- 常量序列除零；
- 只有越界点却宣称完整 SPC；
- 浏览器作为权威控制限计算器。

### TMS 计算位置

- 后端按批准的顺序、分组和 Phase I baseline 计算；
- 长历史或多参数 SPC 由 Worker 执行；
- 返回 baseline version、included sequence range、boundary reset、rule hits 和 limit provenance；
- 前端只渲染点、线、分组边界和命中说明。

## 5. TMS 规则门禁

以下门禁必须在图表能力开放前由 API/Domain/Worker 和 Golden 固化。任一关键门禁失败时，只关闭依赖能力并返回原因，不生成看似有效的结果。

| Gate | 必须满足 | 失败行为 |
|---|---|---|
| `A-G01 Dataset Context` | 固定 Dataset ID/Version、Stage、Current/PUBLISHED/Owner 权限和规范化 Filter Hash | `CONTEXT_INVALID`，不计算 |
| `A-G02 Parameter Identity` | 参数名称/代码、Occurrence/Step、单位、条件和 Stage 兼容 | `PARAMETER_INCOMPATIBLE` |
| `A-G03 Wafer Identity` | CP 必须使用 `Lot_ID + Wafer_ID`；多 Dataset 还需 Dataset Version | 不得仅按 Wafer_ID 合并 |
| `A-G04 Bin Rule` | Pass Bin/Bin Mapping 来自版本化 Cleaner/Rule | `BIN_RULE_MISSING`；不默认 1 |
| `A-G05 Spec Binding` | Lot 级 Spec Version 绑定；LSL<=USL；单侧方向明确 | `SPEC_MISSING` / `SPEC_DIRECTION_INVALID` |
| `A-G06 Value Inclusion` | 数值、NULL、非有限值、哨兵、retest 和排除规则有版本 | 不静默删值；返回 counts/warnings |
| `A-G07 Statistical Policy` | quantile method、ddof、minimum N、zero variance 和精度冻结 | `NOT_ASSESSABLE`，不返回伪 0 |
| `A-G08 Sampling` | 权威统计用全 included population；显示采样确定性且保留风险点 | 返回 sampling summary 和 sample hash |
| `A-G09 Spatial` | X/Y 有效、0 语义明确、Layout/方向兼容、复测和覆盖分母冻结 | 关闭空间能力，不用假 Map |
| `A-G10 Capability` | Cpk/Ppk sigma estimator 分开；稳定性/正态性/分层状态可见 | 降级为描述统计或带警告试算 |
| `A-G11 PAT Rule` | Rule Owner、参考群、k、Robust Sigma、Spec clamp、minimum N 已批准 | 只允许试算，不作为放行/Bin |
| `A-G12 SPC Rule` | 顺序字段、Phase I baseline、subgroup、MR reset、rule set 已批准 | `SPC_RULE_NOT_READY` |
| `A-G13 Traceability` | 返回 input/included/excluded/missing/sample counts、rule versions、computed_at | 响应不完整即不允许导出正式报告 |
| `A-G14 Evidence Language` | Correlation/空间形态只描述证据和候选原因 | 禁止输出已确认根因 |

### 5.1 建议计算分层

| 层 | 允许职责 | 禁止职责 |
|---|---|---|
| React/ECharts | 参数选择、图层显隐、缩放、轴范围、Hover、下载请求 | 分位数、相关、Cpk/Ppk、PAT、SPC、Spec/Bin 推断 |
| 同步 Backend | Box 统计、Histogram bins、Correlation、Capability、轻量 Zone/Overlay 聚合 | 读取任意客户端路径、猜单位/Spec/Bin |
| Worker / Quick Workspace | 大规模精确 PAT、长序列 SPC、大型空间聚合、受控报告 | 把 Quick 临时结果冒充正式 Measurement |
| Saved Analysis / Result | 固定 Dataset Version、Filter Hash、Rule Version、结果和告警 | 只保存前端截图而丢失计算上下文 |

## 6. Golden 测试向量

以下是最低 Golden 集，不代表全部业务验收。数值 Golden 应同时覆盖后端 kernel、API response 和前端渲染合同。

### G-BX-01：线性分位数、实际须线和离群点

输入：

```text
[1, 2, 3, 4, 5, 6, 7, 100]
```

按 NumPy/Pandas linear quantile 期望：

```text
N = 8
Q1 = 2.75
Median = 4.5
Q3 = 6.25
IQR = 3.5
理论下/上阈值 = -2.5 / 11.5
实际下/上须线 = 1 / 7
Outliers = [100]
```

### G-BX-02：0 须线和跨 Lot 同片号

输入 A：`[-100,0,0,0,0,0,0,0]`。

期望：Q1=Median=Q3=0，实际上下须线均为 0，`-100` 是离群点；不得因 JavaScript `0 || min` 把下须线改成 -100。

输入 B：`LOT-A/W1` 和 `LOT-B/W1` 各一组不同数据。

期望：两个独立 Box，不得按 `Wafer_ID=1` 合并。

### G-HN-01：固定直方图区间与样本标准差

输入：

```text
values = [-1, 0, 0, 1]
edges = [-1.5, -0.5, 0.5, 1.5]
```

期望：

```text
counts = [1, 2, 1]
sum(counts) = 4
mean = 0
sample std = 0.816496580927726
```

### G-HN-02：零方差与默认不删异常

- `[5,5,5,5]`：所有输出有限；一格包含 4 个值；Normal PDF、Skew/Kurt 标准化结果和 Cpk/Ppk 返回 `ZERO_VARIANCE/NULL`。
- `[0]*20 + [100]`：默认 Raw N=21，直方图计数总和=21；如果用户显式打开 IQR View，必须同时返回 included=20、excluded=1，且默认能力值不得偷偷改成过滤后口径。

### G-CORR-01：pairwise 缺失对齐陷阱

输入：

| Row | x | y | constant |
|---:|---:|---:|---:|
| 1 | 1 | 10 | 5 |
| 2 | 2 | NULL | 5 |
| 3 | 3 | 30 | 5 |
| 4 | 4 | 20 | 5 |
| 5 | 5 | 50 | 5 |
| 6 | NULL | 60 | 5 |

期望：

```text
pairwise rows = 1,3,4,5
pair N = 4
r(x,y) = 0.8285714285714286
r(x,constant) = NULL / ZERO_VARIANCE
```

VDMOS 独立删 NULL 后错位算法会得到约 `0.914991421995628`，Golden 必须明确拒绝该值。

### G-SCAT-01：确定性采样与风险点保留

构造 20,000 个普通点和 5 个超 LSL/USL 点。两次相同请求期望：

- sample hash 完全相同；
- 5 个超限点全部保留；
- original/returned/preserved_out_of_spec_points 明确；
- 权威相关/均值/标准差不随 display max_points 改变。

### G-CPK-01：双侧能力算术核

输入：

```text
values = [-1, 0, 1]
LSL = -3
USL = 3
sample std = 1
```

期望：

```text
mean = 0
Cp = 1
Cpl = 1
Cpu = 1
two-sided capability = 1
```

该向量用于算术 kernel；服务层如果规定 minimum N=30，应返回 `INSUFFICIENT_N`，不能为了通过 Golden 放宽门禁。

### G-CPK-02：单侧、零方差、反向 Spec、多 Lot

- 只有 USL=3：Cpu=1，Cp=NULL；一侧能力名称按批准规则显示。
- 只有 LSL=-3：Cpl=1，Cp=NULL。
- 常量 `[5]*30`：`ZERO_VARIANCE`，不得返回 0/Infinity。
- LSL=3、USL=-3：`SPEC_DIRECTION_INVALID`，不得自动交换。
- `LOT-A` Spec `[0,10]`、`LOT-B` Spec `[0,100]`：分别计算；禁止合并后套首 Lot Spec。
- `[0,1]`：sample std=0.7071067811865476，用于捕获误用 population std。

### G-MAP-01：坐标 0、Pass Bin、Spec 边界和复测

最小输入应包含：

| Lot | Wafer | X | Y | Seq | Bin | P |
|---|---|---:|---:|---:|---:|---:|
| LOT-A | 1 | 0 | 0 | 1 | 2 | -1 |
| LOT-A | 1 | 0 | 0 | 2 | 3 | 5 |
| LOT-A | 1 | 1 | 0 | 3 | 2 | 10 |
| LOT-A | 1 | 0 | 1 | 4 | 2 | 11 |
| LOT-B | 1 | 0 | 0 | 1 | 2 | 5 |

规则：Pass Bin=2，参数 Spec `[0,10]`。

期望：

- 坐标 `(0,0)` 保留；
- 物理 Wafer 数=2；
- LOT-A `(0,0)` 复测数=2，Bin 图按最高不良优先级显示 fail；
- LOT-B/W1 不与 LOT-A/W1 合并；
- P=-1 为低超限，P=11 为高超限，P=0/10 位于规格内；
- Bin=1 不得被默认当 Pass，因为本向量 Pass Bin=2。

### G-ZONE-01：径向分类和平移/旋转不变性

一片完整测试 Wafer 使用：

```text
(0,0)
(±0.5,0), (0,±0.5)
(±1,0), (0,±1)
```

在 0.33/0.66 规则下期望：

- `(0,0)` = Center；
- 半径 0.5 = Mid；
- 半径 1 = Edge。

将全部坐标平移 `(100,-50)` 或旋转 90°，Zone 不变。全坐标 `(0,0)` 必须返回 `SPATIAL_COORDINATES_UNAVAILABLE`，不能把全部 Die 判成 Center。缺少外圈时应返回 coverage/layout 告警，不能默默把观测最远点当真实 Edge。

### G-OV-01：按物理 Wafer prevalence，而不是记录率

同一坐标在三片 Wafer：

- W1：1 条 fail；
- W2：1 条 pass；
- W3：2 条 fail 复测。

期望 fail wafer prevalence=`2/3=66.6667%`；不得按 raw records 得到 `3/4=75%`。响应同时返回 numerator wafers=2、covered wafers=3、raw records=4、duplicate/retest records=1。

### G-HEAT-01：跨 Wafer 固定色域

- W1 参数值 `[0,5,10]`；
- W2 参数值 `[5,50,100]`；
- 固定全局色域 `[0,100]`。

期望两个 Wafer 中 value=5 的颜色完全相同。不得按每片 min/max 产生不同颜色。

### G-PAT-01：Robust Sigma 和异常证据

输入：

```text
values = [0,1,2,3,4,5,6,7,8,9,100]
quantile = linear
k = 3
```

期望：

```text
Q1 = 2.5
Median = 5
Q3 = 7.5
IQR = 5
Robust Sigma = 5 / 1.34898 = 3.706504173523699
PAT_L = -6.119512520571098
PAT_U = 16.1195125205711
Outliers = [100]
valid N = 11
outlier rate = 1/11
```

### G-PAT-02：常量、缺失、非法 k 和分层

- `[5]*30`：不得强制 IQR=0.0001；返回 zero-dispersion 的批准语义。
- 20 行中只有 10 个有效值：异常率分母必须是 valid N=10。
- k=0、负数、NaN、Infinity：请求拒绝，不得回退 5。
- 两片 Wafer 各自稳定但均值明显偏移：按批准参考群分别计算，不得默认 pooled 后掩盖 shift。

### G-SPC-01：I-MR 公式

按 Seq 排序输入：

```text
[10, 10, 10, 20]
```

期望：

```text
CL = 12.5
MR = [0,0,10]
MRbar = 3.3333333333333335
LCL = 3.633333333333333
UCL = 21.366666666666667
MR_UCL = 10.89
```

该向量用于公式 kernel；服务层 minimum N/Phase I gate 独立验证。

### G-SPC-02：顺序、Wafer 边界、常量和显示范围

- 将 G-SPC-01 记录按存储顺序打乱，但保留 Seq；结果必须与按 Seq 输入相同。
- W1 全部 10，W2 全部 20；MR 必须在 Wafer 边界重置，不得产生 10 的跨片 MR。
- `[5]*10`：CL=UCL=LCL=5，MRbar=0；绘图全部有限，不得除 0。
- 全负值序列：自动轴必须用跨度 padding，不得用简单乘 0.95/1.05 导致反向或裁剪。
- 11 片数据：图、统计、控制限必须使用同一 resolved scope，禁止只画前 10 片却用 11 片算限。

## 7. 迁移优先级

### P0：先冻结规则和纠正错误

1. Canonical Filter/Rule/Spec/Bin/Parameter Identity 合同；
2. 统一 quantile、ddof、minimum N、zero variance；
3. 正确 pairwise correlation；
4. Cpk/Ppk sigma 语义和 Lot Spec；
5. 坐标 0、复测、physical wafer denominator；
6. PAT 参考群和 SPC 顺序/Phase I；
7. 所有响应的 counts、sampling 和 rule provenance。

### P1：迁入核心展示能力

1. BoxPlot；
2. Histogram/Normal 描述视图；
3. Wafer Distribution Scatter 和独立 X/Y Scatter；
4. Correlation Matrix；
5. Cpk/Ppk 表；
6. Parameter Heatmap、Zone、Overlay、Wafer Prevalence。

### P2：规则型和高成本能力

1. 正式 PAT；
2. SPC I-MR 和后续 run/trend rules；
3. Saved Analysis、报告和受控 Export；
4. 大规模 Worker、缓存和性能验收。

P2 不应在 P0 Rule Owner 和 Golden 未关闭时提前开放为正式结论。

## 8. 复用分类清单

### 8.1 可直接借鉴的纯展示逻辑

- 逐参数图卡、参数选择器和多图布局；
- Lot/Wafer 分组颜色；
- LSL/USL/Target/Mean/±3σ 图例开关；
- 手动 X/Y 轴；
- Box 外点层开关，但必须显示隐藏数；
- Histogram 柱顶计数和统计侧栏；
- Correlation 发散色阶；
- Wafer 小图矩阵、轻量/详看、Hover、Zoom；
- Heatmap/Fail Overlay 图层开关；
- I-MR 上下图布局；
- PNG/CSV/报告请求交互。

### 8.2 必须重做计算、可保留交互

- Box 分位数与须线；
- Histogram bin 和 Normal diagnostics；
- X/Y Correlation；
- Cpk/Ppk；
- Zone；
- 多 Wafer Overlay/Prevalence；
- PAT；
- SPC。

### 8.3 完全禁止迁移

- VDMOS 原始厂商解析器；
- HTML 内置 Spec/Bin；
- 缺失 Bin→1；
- 通用 9999 哨兵；
- 默认 IQR 删值；
- 随机采样；
- 独立删 NaN 后错位 Correlation；
- 坐标 0 删除和 parseInt 截断；
- 最后一条复测覆盖；
- Row 三等分 Zone；
- record rate 冒充 wafer prevalence；
- Spec/Control/PAT limit 混名；
- 未经证据的工艺根因文字。

## 9. 已确认、未确认与后续 Gate

### 9.1 已确认

- 两份 VDMOS v8.9 文件 SHA 完全一致；
- 上述算法和危险默认值均可在所列源码行复现；
- 当前 CP Cockpit 用户可见能力包含 Box、Wafer Scatter、Mapping、Zone、Overlay 和 Cpk；
- 当前 CP Cockpit 不包含 Histogram/Normal、pairwise Correlation、PAT 或 SPC；
- 当前 Mapping 已有可复用 Golden 种子；
- 当前 Box、Cpk、Zone/Overlay 没有发现同等直接 Golden 覆盖；
- 当前 Cpk 实现存在 2/5/30 三种 minimum N 口径。

### 9.2 尚未由本审计决定

- TMS 最终 quantile type；
- Cpk/Ppk minimum N、within-sigma estimator 和正常性/稳定性门槛；
- PAT Rule Owner、k、参考群、Spec clamp 和 zero-IQR 语义；
- SPC Phase I baseline、subgroup 和 rule set；
- CP Wafer Layout/方向的权威来源；
- Zone 0.33/0.66 是否为正式业务规则；
- 相关性显著性、多重比较和最大参数数；
- Histogram 默认 bin policy；
- 正式分析与 Quick Workspace 的容量和超时阈值。

这些必须在对应开发包进入实现前由业务 Rule Owner 和真实样本 Golden 关闭，不能由开发者或前端默认。

## 10. 验收检查表

实现任一能力时至少回答：

- [ ] 是否固定 Dataset Version、Filter Hash 和 Rule Version？
- [ ] 是否使用 `Lot_ID + Wafer_ID`，没有仅按片号合并？
- [ ] 是否按 Lot 绑定 Spec，没有取第一份？
- [ ] 是否显式使用 Pass Bin，没有默认 1？
- [ ] 是否返回 input/included/excluded/missing counts？
- [ ] 是否没有静默删 IQR 外点和通用 9999？
- [ ] 是否冻结 quantile、ddof、minimum N 和 zero variance？
- [ ] 是否让显示采样不改变权威统计并保留风险点？
- [ ] 是否保留坐标 0、复测证据和物理 Wafer 分母？
- [ ] 是否区分 Spec、Control、PAT 和 Display Limit？
- [ ] 是否区分 Cpk 和 Ppk sigma？
- [ ] PAT/SPC 是否已有 Rule Owner 和真实 Golden？
- [ ] 是否可从结论下钻到 Canonical 明细和来源？
- [ ] 是否避免把相关/空间形态写成根因？
- [ ] API、前端、导出是否使用同一 Context 和计算版本？

## 11. 最终判断

VDMOS v8.9 的价值在于它把工程师常用的图表集中在一个可操作界面中；它的风险也来自同一件事：解析、规格、Bin、统计、图表和结论全部耦合在浏览器文件里。

TMS 不应复制这一耦合。正确迁移路径是：

1. 用 TMS Canonical 和版本化 Rule 建立唯一计算事实；
2. 用后端/Worker 生成可对账的统计和证据；
3. 用当前 CP Cockpit 的复合身份、失败关闭和 Mapping 经验作为 CP 空间基线；
4. 借鉴 VDMOS 的图表组织和交互；
5. 用本文 Golden 阻止旧算法、危险默认值和口径漂移重新进入系统。

在 P0 门禁未完成前，Histogram/Correlation/Cpk/Ppk/PAT/SPC 和跨 Wafer Overlay 最多只能作为开发试算，不得标记为正式质量判断或生产放行能力。
