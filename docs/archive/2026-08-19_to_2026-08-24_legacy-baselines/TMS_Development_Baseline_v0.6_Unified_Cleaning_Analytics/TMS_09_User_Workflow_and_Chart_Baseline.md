# TMS v0.6 最终用户流程与图表基线

## 1. 产品原则

最终用户面对的是“数据任务、清洗结果、分析图表和交付物”，不是厂商 Parser 目录。厂商与格式信息作为可追溯元数据展示，只有管理员在格式治理页面维护。

## 2. 数据接入流程

CP与FT使用两个独立任务入口和两套Cleaner调用链。公共任务状态仅统一调度与审计，不共享源文件解析逻辑。

### 2.1 新建任务

用户选择CP / FT / 独立业务汇总、厂家（或保守Auto Detect）、一个或多个文件/压缩包和任务说明。CP以晶圆厂与Lot为主，Product仅在源数据存在或人工明确补录时选择；FT以Product为主，Lot仅在源数据存在时保存。提交前显示文件名、大小、SHA状态、文件角色和检测结果；检测不唯一时只能进入待确认。

Cleaner完成后页面分别展示“源程序已识别字段”和“可人工补录字段”。补录值不修改源解析结果；每个值记录SOURCE/MANUAL/MAPPING/NOT_PROVIDED/IGNORED来源状态。当前分析不需要的缺失字段可以明确忽略。

### 2.2 任务进度

```text
UPLOADING → DETECTING → PARSING → NORMALIZING → VALIDATING
→ READY_FOR_REVIEW → PUBLISHED / FAILED / CANCELLED
```

页面展示文件级进度、行数、警告/错误摘要和修复建议。关闭浏览器不影响 Worker。

### 2.3 发布确认

发布前至少核对输入文件与SHA、Format Profile/Cleaner Release、源行/Unit/Measurement数量、该Stage实际必需的业务身份、PASS/FAIL/Bin、单位与Spec/Bin Resolution、DQ以及迁移期旧系统差异。CP缺Product、FT缺Lot不属于发布阻断。只有规则允许的ERROR可由授权用户填写原因后Waive；BLOCKER不允许Waive。

## 3. 已发布数据集页面

页面顶部固定显示Dataset Name/Version/Status、Stage、Processing Run/Cleaner Version、Spec-Bin-Evaluation Context、发布时间与发布人，并按Stage显示身份：CP显示晶圆厂/Lot/Wafer，FT显示Product及源数据实际存在的可选Lot/供应商信息。

主要区域为：来源与 DQ、清洗结果摘要、明细、图表、保存分析、导出/报告、历史版本。

## 4. 全局筛选合同

CP：晶圆厂、Lot、Wafer、Parameter默认全部并支持单选/多选；辅助筛选包括Bin/Result、时间、Site和Program。Product不是CP分析前提。

FT：Product、Parameter、测试条件默认授权范围内全部并支持单选/多选；辅助筛选包括PASS/FAIL、Soft/Hard Bin、机台、程序、时间，以及源数据实际存在时的Supplier/Lot。Lot不是FT分析前提。

每个统计响应返回规范化 `filter_summary`。图表、明细和导出必须使用同一个筛选对象。多 Lot CP 按每行 Lot 上下文匹配 Spec，禁止使用第一份或最新一份规格代替。

## 5. 图表分组

### 5.1 Overview

- 总量、PASS、FAIL、Yield、DQ；
- Lot/Wafer/Test Batch 良率趋势；
- Bin Pareto；
- 超限、低 Cpk/PAT 风险摘要。

### 5.2 明细与钻取

- CP Die / FT Unit Wide View；
- Measurement Long View；
- Die/Unit 身份、Bin、原始值、评价和来源行；
- 服务端分页、排序、过滤与 Export Job。

### 5.3 参数分析

- BoxPlot、Histogram/Normal、Scatter、Correlation；
- Cpk/Ppk、单值/多值趋势；
- Spec、PAT 等评价层可切换，不混成一个 PASS/FAIL。

### 5.4 CP 空间分析

- BIN Wafer Map、Parameter Heatmap；
- 多 Wafer 复合失效图；
- Parameter + Fail Bin Overlay；
- Edge/Center/Quadrant 区域比较和坐标钻取。

只有源数据提供可信 `Lot_ID + Wafer_ID + X + Y` 时才开放空间分析。

### 5.5 FT 质量分析

- 测试序号/批次多参数 Scatter；
- PAT 参数表和异常点钻取；
- SYL/SBL、Yield 和 Fail Bin；
- 按条件、机台、程序或批次对比。

### 5.6 SPC 与动态规则

I-MR/SPC、PAT/SBL、Margin 与超限结果都显示算法版本、样本数、排除规则、阈值和计算时间。

## 6. 统计权威边界

后端负责 Yield/Bin、Cpk/Ppk、PAT/SBL、SPC、相关性、采样、规格匹配、单位换算和排除规则，并返回 Evaluation Run、Rule Version、Dataset Version 与 Filter Summary。

前端只允许缩放、Brush、排序、显隐、颜色、显示精度，以及将交互转成规范化筛选重新请求后端。

## 7. 导出合同

所有业务导出由后端创建 Export Job；当前页快速导出也必须登记事件。导出记录 Dataset Version、Filter Summary、Spec/Bin/Evaluation Context、Template Version、请求人、文件 SHA、保留期限及兼容/平台输出类型。

支持 CSV、XLSX、图片、HTML/PDF 报告和批准的 BIN-TXT。下载链接短时有效，不暴露物理路径。

## 8. 页面验收

1. 首次进入不自动触发全部高成本图表；确认范围后绘制。
2. Lot/Wafer/参数全选、单选、多选结果一致。
3. 图表、明细和导出使用同一 Dataset Version 与筛选摘要。
4. 多 Lot 规格不串用；图表不静默删除异常点或修改值。
5. 统计结论可钻取到 Measurement、规则版本和输入文件。
6. 未授权用户不能通过 URL、导出或下载链接访问数据。
7. 代表性桌面分辨率完成真实渲染检查，不能只验证源代码。
