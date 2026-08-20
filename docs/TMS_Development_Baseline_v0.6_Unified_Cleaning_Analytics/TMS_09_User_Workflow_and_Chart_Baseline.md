# TMS v0.6 最终用户流程与图表基线

## 1. 产品原则

最终用户面对的是“数据任务、清洗结果、分析图表和交付物”，不是厂商 Parser 目录。厂商与格式信息作为可追溯元数据展示，只有管理员在格式治理页面维护。

## 2. 数据接入流程

### 2.1 新建任务

用户选择 CP / FT / 独立业务汇总、厂家（或保守 Auto Detect）、一个或多个文件/压缩包、目标 Product/Project 和任务说明。提交前显示文件名、大小、SHA 状态、文件角色和检测结果；检测不唯一时只能进入待确认。

### 2.2 任务进度

```text
UPLOADING → DETECTING → PARSING → NORMALIZING → VALIDATING
→ READY_FOR_REVIEW → PUBLISHED / FAILED / CANCELLED
```

页面展示文件级进度、行数、警告/错误摘要和修复建议。关闭浏览器不影响 Worker。

### 2.3 发布确认

发布前至少核对输入文件与 SHA、Format Profile/Cleaner Release、源行/Unit/Measurement 数量、Lot/Wafer/批次、PASS/FAIL/Bin、单位与 Spec/Bin Resolution、DQ 以及迁移期旧系统差异。只有规则允许的 ERROR 可由授权用户填写原因后 Waive；BLOCKER 不允许 Waive。

## 3. 已发布数据集页面

页面顶部固定显示 Dataset Name/Version/Status、Stage/Supplier/Product、Lot 或 Test Batch、Processing Run/Cleaner Version、Spec-Bin-Evaluation Context、发布时间与发布人。

主要区域为：来源与 DQ、清洗结果摘要、明细、图表、保存分析、导出/报告、历史版本。

## 4. 全局筛选合同

CP：Lot、Wafer、Parameter 默认全部并支持单选/多选；辅助筛选包括 Bin/Result、时间、Site 和 Program。

FT：Product、Supplier、Test Lot/制造批次、Parameter 默认授权范围内全部并支持单选/多选；辅助筛选包括 PASS/FAIL、Soft/Hard Bin、机台、程序、时间和测试条件。

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
