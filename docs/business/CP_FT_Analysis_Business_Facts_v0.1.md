# CP/FT分析业务事实与开发约束 v0.1

> **状态说明（2026-08-27）**：本文是早期事实梳理。其中“FT Lot 不是正式发布必填身份”“缺 Lot 不阻断正式分析”等结论已被后续业务决定取代。当前正式 CP/FT Route A 的 Lot 必须来自 Cleaner 或受审计的文件级人工确认；缺失时暂停为 `NEEDS_INPUT`，不得以空值、默认值或任务级猜测发布。现行合同见 [`TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md`](../architecture/TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md)。

状态：历史业务事实；与 `TMS_Business_Requirements_v0.2.md` 冲突时以 v0.2 为准

确认日期：2026-08-21  
用途：作为TMS数据模型、清洗接入、分析筛选和验收的业务事实依据。

## 1. CP分析事实

CP源数据通常只有批次和晶圆测试信息，不保证存在产品型号。

CP分析必需信息：晶圆厂/测试来源、Lot批次号、参数名称、测试条件、测试数值，以及源数据实际提供的Wafer、坐标、Bin、规格和单位。

Product不是CP发布和分析的必填身份。源目录或文件能够提供Product时，可作为可选业务补充信息保存；没有Product不得阻断CP数据。

```text
晶圆厂 → Lot → Wafer → Die坐标/Bin → 参数 + 测试条件 + 数值
```

## 2. FT分析事实

FT源数据通常有产品型号和测试信息，但不保证存在Lot批次号。

FT分析必需信息：Product产品型号、参数名称、测试条件、测试数值，以及源数据实际提供的Unit、PASS/FAIL、Bin、机台和程序。

Lot不是FT发布和分析的必填身份。源数据存在Lot时原样保存；没有Lot不得伪造、补默认值或阻断仅依赖Product/参数/条件/数值的FT分析。

```text
Product → Unit/测试序号 → 参数 + 测试条件 + 数值
```

## 3. 分阶段身份约束

| Stage | 必填身份 | 可选身份 | 禁止行为 |
|---|---|---|---|
| CP | 晶圆厂/来源、Lot | Product、Wafer及其他源字段 | 因缺Product阻断；用Lot冒充Product |
| FT | Product | Lot、供应商/封测厂及其他源字段 | 因缺Lot阻断；生成虚假Lot |

共同原则：只保存源数据、批准映射或人工明确补录的事实；缺失字段保持缺失，不用推断值填满统一模型。

数据库是CP/FT业务字段的能力全集，不表示每种源文件都必须提供所有字段。每个字段必须区分“源文件提供”“人工补录”“批准映射”“未提供”；人工补录不得反写或篡改原始文件解析结果。

## 4. 现有清洗代码复用约束

CP清洗与解析以 `F:\cp_data_ansys`（业务称CP_anays）中的成熟实现为来源，FT清洗与解析以 `F:\data_IGBT_multiple` 中的成熟实现为来源。

CP Cleaner与FT Cleaner是两套独立程序、独立输入合同和独立业务流程。不得建设一个同时猜测CP/FT字段的通用Cleaner。两条流程只在各自完成清洗以后，通过不同Adapter映射到公共数据库模型：

```text
CP文件 → CP格式Reader/Cleaner → CP结果合同 → CP Adapter ┐
                                                      ├→ Canonical Model → 分Stage分析
FT文件 → FT格式Reader/Cleaner → FT结果合同 → FT Adapter ┘
```

TMS接入层只负责：

1. 识别应调用的既有Cleaner/Reader；
2. 传入源文件并接收既有清洗结果；
3. 通过Adapter映射到Canonical Model；
4. 补充Dataset Version、来源文件和运行血缘；
5. 用同一真实样本对账旧系统与TMS结果。

除非已证明既有实现无法表达新格式，禁止在TMS内重复编写同一厂商Parser、单位换算、Bin/Yield或参数拆解逻辑。确需修改时，优先修复或抽取既有实现，再由Adapter复用。

当前 `backend/app/cleaners/huahong_dcp.py` 视为G0技术验证实现，不作为继续扩展多厂商Parser的模板；后续华虹正式接入需与 `cp_data_ansys` 既有华虹流程逐字段、逐Wafer对账并收敛到复用Adapter。

## 5. HTML图表复用约束

`历史项目-参考用/fjd项目/VDMOS_Tool_v8.9.html` 已包含可用图表交互和计算逻辑。新平台开发以迁移复用为主：

- 保留图表类型、筛选交互、统计字段、Tooltip、颜色和钻取关系；
- 将HTML中的数据输入替换为Dataset Version后端接口；
- 将可直接复用的图表配置和纯计算函数抽取成TypeScript模块；
- 不重新设计已有Wafer Map、BoxPlot、Scatter、Pareto、分布、相关性、SPC、PAT等逻辑；
- 只有与本业务事实冲突的默认值才调整，例如不能假定CP有Product、FT有Lot，也不能用推算规格代替真实规格。

每张迁移图表需记录原HTML功能位置、迁移模块、输入字段、修改点和对账结果。

## 6. 当前基础需求：人工补录

人工补录不是未知格式识别模块的附属功能，而是CP/FT接入的基础能力。源程序先输出能够确定的源字段，界面再按Stage显示缺失但可补充的业务字段：

- CP补录区：晶圆厂/来源、可选Product、项目等；Lot和测试事实优先来自CP Cleaner；
- FT补录区：Product、可选Lot、封测厂/项目等；参数、测试条件和数值优先来自FT Cleaner；
- 能够分析且不需要补录的任务直接进入分析；
- 需要补录的字段由用户明确填写；
- 不适用或不需要的字段由用户明确标记忽略。

补录记录至少包含Stage、字段、值、适用文件/批次、操作者、时间和说明。程序组合“源字段+人工字段”形成分析上下文，但源字段和人工字段的来源状态始终可区分。

## 7. 进阶需求：格式自动识别

该模块暂不提前开发。当出现大量未知格式或反复人工判断时，再启动建设。它识别的是“该调用哪个既有CP/FT Cleaner以及字段如何映射”，不替代CP/FT独立Cleaner。

```text
源文件 → 格式特征识别 → 已知格式自动调用既有Cleaner
                        → 不唯一/未知格式进入人工确认
解析结果 → 与基础人工补录流程衔接
确认结果 → 保存为版本化Format Profile → 后续同类文件自动识别
```

启动该模块前先盘点实际未知格式数量、重复频率、人工耗时和对分析的影响，再确定识别特征、置信度、UI和治理流程。

## 8. 当前开发优先级

1. 修正CP Product与FT Lot的错误必填假设；
2. 建立相互独立的CP Cleaner Adapter与FT Cleaner Adapter并完成真实样本对账；
3. 建立按Stage显示的人工补录基础界面和字段来源记录；
4. 按HTML逐图迁移现有图表；
5. 完成CP与FT各自主线分析；
6. 达到实际触发条件后再建设格式自动识别模块。
