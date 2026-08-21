# TMS v0.6 参考项目能力映射

## 1. 目的

本文件记录 `cp_data_ansys`、`data_IGBT_multiple` 和 `VDMOS_Tool_v8.9.html` 对新平台的事实依据。参考实现中的业务规则只有经过证据确认、版本化和审批后才能进入生产主线。

## 2. CP 能力清单

| 厂家/工作流 | 已观察输入 | 当前结果 | v0.6 接入要求 |
|---|---|---|---|
| 华虹 HH | DCP/TXT；目录、ZIP、7z | cleaned/yield/spec、图表 | 保留目录层级、Lot/Wafer/X/Y/Bin、单位和 pass_bin；容器只属于输入层 |
| JT/Jetech | Excel；目录、ZIP | cleaned/yield/spec、图表 | 源列映射进入 Format Profile，不进入 UI 条件分支 |
| Lion CP V1/V2 | 已批准 Excel 结构 | cleaned/yield；单/多 Lot spec | 精确有序 schema；未知或混合版本拒绝；多 Lot 必须逐 Lot 匹配 Spec |
| 扬州国宇 FRD | Excel、多层目录、ZIP | cleaned/yield/spec | 工程单位显式换算；没有真实坐标时禁止伪造 Wafer Map |
| Lion 管芯数 | 两种已批准月报 Excel | Wafer 级五列汇总 | 独立业务 Dataset Type，不强塞进 Die Measurement 模型 |

CP 标准输出的共同语义是：

```text
Lot_ID + Wafer_ID + X + Y + Seq + Raw Bin + Parameters
Wafer Yield / Dynamic Fail Bin
Parameter Unit / Limit / Test Condition / Lot Scope
```

旧项目的 cleaned/yield/spec 是兼容输出合同；新平台内部以 Dataset Version 和 Canonical Long Measurement 为权威，仍可按目标厂商合同生成兼容文件。

## 3. FT 能力清单

| 厂家/工作流 | 已观察格式 | 当前结果 | v0.6 接入要求 |
|---|---|---|---|
| 日月新 ASE | 分目录 XLSX | DC/DVDS/RG、PAT、SYL/SBL、Scatter | 文件角色必须固化；参数、单位、上下限和 Bias 可追溯 |
| 杰群 | 分目录 DTA CSV、统一 CSV、第三产线 | DC/DVDS/RG、PAT、Yield、Scatter | 依据目录和头部签名保守识别；混合格式拒绝；避免参数子串冲突 |
| 电基 | PowerTECH 文本/原生 XLSX、STS8203、TF CSV | FT-ALL、PAT、SYL/SBL、Scatter | 每个已批准布局单独版本；产品、文件名、元数据、列序和单位严格校验 |
| 集佳 | STS8203 GB18030 CSV | ASE 风格 DC_Data | 保留 PASS/FAIL 行；失败后未测参数保持空；兼容导出不等于内部事实模型 |

FT 参数名称不能只保存显示文本。Canonical Parameter Identity 至少包含：

```text
parameter_code
occurrence/step
bias polarity + value + unit
measurement unit
test condition
source item identity
```

## 4. HTML 图表能力清单

源码确认的主要能力包括：

- BIN 总览、良率对比和失效 Pareto；
- Summary、BoxPlot、Cpk、Scatter、单值/多值和趋势；
- Wafer Map、参数热力图、复合失效 Map、参数/Bin 叠加和区域分析；
- 分布、正态拟合、相关性、好品/坏品分布和 Bin 共现；
- PAT、SPC I-MR、规格裕度、超限率、Wafer Summary；
- Lot/Wafer/参数选择、Y 轴范围、颜色范围、PNG/CSV/Excel/BIN-TXT/报告导出；
- 浏览器本地项目保存/恢复。

这些能力用于确定“用户想看什么”，不用于决定“数据如何清洗或统计”。

## 5. 明确禁止复制的行为

| 参考行为 | 风险 | v0.6 规则 |
|---|---|---|
| 默认仅 `BIN=1` | 不同厂家 pass_bin 不同 | 由版本化 Bin Mapping 决定，UI 展示实际规则 |
| 图表前 IQR 静默剔除 | 图表与事实不一致 | 默认不删除；若启用分析过滤，返回排除规则、数量和版本 |
| 找不到产品 Spec 时合并全部 Spec 或取第一份 | 多 Lot/产品串规格 | `NO_MATCH` 或 `CONFIG_AMBIGUOUS`；不得回退到无证据规格 |
| 浏览器解析原始文件并计算权威 Cpk/PAT/SPC | 规则分叉且不可审计 | 后端版本化服务计算；前端只做展示和可逆交互 |
| 用 localStorage 作为正式项目存储 | 多用户、权限和版本不可控 | 正式 Saved Analysis 存服务端；本地偏好只保存非业务 UI 设置 |
| 平铺二十多个 Tab | 用户难以理解业务路径 | 按 Overview、明细、参数、空间、质量/统计、报告分组，渐进展开 |

## 6. 迁移原则

1. 既有CP/FT Cleaner是实现来源，TMS通过Adapter调用和映射，禁止无依据重写同一解析、清洗和统计逻辑。
2. 先登记Format Profile和代表样例，不从旧代码数量推断“已支持”。
3. 每个格式独立验收文件数、行数、Stage必需身份、Wafer/Unit、Bin、参数、单位、规格和代表值。
4. CP缺Product、FT缺Lot是正常业务状态，不作为格式失败；禁止为填满模型而伪造值。
5. 兼容导出与内部Canonical Model分开维护。
6. 图表以`VDMOS_Tool_v8.9.html`的现有逻辑为迁移来源，只替换数据接入和已确认冲突的业务默认值。
7. 图表必须读取明确Dataset Version；不得扫描“最新文件”。
8. 未迁移格式继续由旧系统生产使用，直至新平台与既有实现真实样本对账通过。

## 7. 本次检查边界

两个 Python 项目以当前仓库文档、注册表、配置和实现路径为依据。HTML 已完成源码级结构与公式检查；本地浏览器安全策略阻止 `file://` 页面加载，因此本次没有把实际渲染效果标记为已验收。
