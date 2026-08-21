# CP/FT独立接入与人工补录架构 v0.1

## 1. 架构决定

CP与FT从任务入口、格式识别、Cleaner调用、结果合同到人工补录表单均保持独立。公共数据库不反向要求两类源文件提供相同字段。

| 层级 | CP | FT | 公共层 |
|---|---|---|---|
| 任务入口 | CP文件、晶圆厂 | FT文件、Product | 文件登记、任务状态 |
| 清洗实现 | `F:\cp_data_ansys` | `F:\data_IGBT_multiple` | 不实现通用Cleaner |
| 结果合同 | Lot/Wafer/坐标/Bin/参数/条件/数值 | Product/Unit/结果/Bin/参数/条件/数值 | Run/Unit/Test Item/Measurement |
| 人工补录 | 来源、可选Product/项目 | Product、可选Lot/来源/项目 | 字段来源、操作者、适用范围 |
| 分析入口 | 晶圆厂/Lot/Wafer | Product/条件 | Dataset Version |

## 2. 字段来源状态

平台字段值必须带来源语义：

- `SOURCE`：源文件由既有Cleaner解析；
- `MANUAL`：用户在CP或FT补录界面明确填写；
- `MAPPING`：命中已批准的主数据映射；
- `NOT_PROVIDED`：源文件没有且当前分析不需要；
- `IGNORED`：用户明确声明该字段在指定范围不使用。

优先级不由字段是否非空决定。源值与人工值冲突时进入确认，不静默覆盖。最终分析值必须能够回溯到来源记录。

## 3. 基础补录流程

```text
选择CP或FT任务
  → 调用对应既有Cleaner
  → 展示已识别字段和来源
  → 展示当前Stage可补录字段
  → 用户填写/确认忽略
  → Adapter合成分析上下文
  → DQ检查该Stage真正必需的信息
  → Dataset Version
```

人工补录表单由Stage字段合同驱动，不把数据库所有字段一次性展示给用户。

## 4. 实施边界

当前先实现CP/FT独立Adapter与基础补录合同。自动格式识别器、识别置信度、未知格式学习和可视化字段映射器，在未知格式形成实际规模后单独立项。

图表继续迁移 `VDMOS_Tool_v8.9.html` 的已有逻辑；CP图表读取CP分析上下文，FT图表读取FT分析上下文，不依赖另一Stage缺失的身份字段。
