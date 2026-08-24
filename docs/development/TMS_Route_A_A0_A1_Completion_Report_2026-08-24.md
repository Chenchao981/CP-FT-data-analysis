# TMS Route A A0/A1 开发总结报告

日期：2026-08-24

对应规划：A0 基线收敛、A1 Cleaner Worker 底座

代码提交：`936b11c feat: add route A worker foundation`

## 一、本轮完成了什么

1. 完成真实数据库和现有 Cleaner 的开发前盘点。
2. 将开发数据库升级到 `sql2014_0010`。
3. 删除已经确认没有数据、没有外部依赖的 Route B 明细表：
   - `analysis.run`
   - `analysis.unit`
   - `analysis.test_item`
   - `analysis.measurement`
4. 保留 `analysis.saved_analysis`，因为它属于分析配置，不是第二套明细事实。
5. 建立 Cleaner Release 可执行合同，登记：
   - 厂家和测试阶段；
   - Cleaner 包地址和 Python Runtime；
   - Entrypoint 和 Adapter；
   - 输入、输出合同版本；
   - SHA256；
   - 执行参数、超时和最大输出体积。
6. 将当前华虹 CP Cleaner 和日月新 FT Cleaner 注册为 Released 版本。
7. 建立 SQL Server Job Queue，支持：
   - 幂等键；
   - Worker 租约；
   - 心跳续租；
   - Worker 异常后的过期恢复；
   - 最大尝试次数；
   - 按任务类型领取。
8. 建立独立 Route A Worker，由系统后台调用原 Python Cleaner。
9. 上传接口改为“保存原始文件、创建任务、返回 QUEUED”，不再在 Web 请求中同步运行 Cleaner。
10. 前端增加排队、处理中状态和自动刷新。
11. 增加临时 Cleaner Artifact 登记，保存文件角色、SHA256、体积和到期时间。
12. 将上传记录、结果、文件下载、Dataset 和人工补录权限收紧为任务上传者本人或系统管理员。
13. CP 人工补录字段增加 `LOT_ID`。
14. 使用真实华虹 ZIP、真实 CP Cleaner 和真实 SQL Server 完成端到端验证。

## 二、完成得比较好的地方

### 1. 保留了原 Python Cleaner

系统没有重写清洗算法，而是通过 Cleaner Release 和 Adapter 调用已经验证过的程序。这符合当前低并发业务规模，也方便后续增加新的晶圆厂 Cleaner。

### 2. 清除了两套 Canonical 明细模型并存的问题

Route B 的四张空明细表已经在有数据保护条件下删除。后续正式数据只进入 `test.*`，避免同一份 CP/FT 数据在两套模型中出现不同答案。

### 3. Cleaner 版本具备可追溯性

Worker 执行前校验 Cleaner 包 SHA256，并从数据库读取执行参数。后续 Cleaner 更新时可以发布新版本，不需要在系统调用代码中修改清洗规则。

### 4. 上传请求和清洗执行已经解耦

用户上传后可以快速得到任务编号和排队状态。Cleaner 执行时间较长或异常时，不会占住 Web 请求。

### 5. 队列基础可靠性经过真实验证

已经验证任务幂等、两个 Worker 争抢同一任务、心跳续租、Worker 崩溃后的租约恢复，以及未注册任务类型不会被误消费。

### 6. 完成范围没有被夸大

当前只把 Cleaner 输出登记为临时 Artifact 和结果摘要，没有把它描述成“正式结构化入库已完成”。A2 的边界保持清楚。

## 三、开发中不确定或目前不够好的地方

### 1. 当前 Cleaner 输出与业务口径不完全一致

业务讨论中提到三个 XLSX，但当前实际发布包输出为：

- 华虹 CP：cleaned、yield、spec 三类 CSV；
- 日月新 FT：cleaned XLSX，加 scatter data、spec、manifest。

本轮按实际程序输出登记了版本化合同，但后续 Cleaner 如果改成三个 XLSX，需要发布新输出合同和新 Adapter，不能直接覆盖旧合同。

### 2. 尚未完成正式结构化入库

Cleaner 目前执行成功后只生成结果摘要和临时文件记录，还没有写入：

- `test.test_run`
- `test.unit_result`
- `test.measurement`

因此当前还不能依靠数据库明细完成完整的 Wafer Map、参数趋势和跨批次二次分析。

### 3. 多 Lot Spec 规则尚未落地

已经确认的业务规则是“相同 Spec 共用，不同 Spec 按 Lot 保存”，但尚未完成：

- 如何判定两份 Spec 完全相同；
- 单位换算后再比较还是原值比较；
- 缺失上下限如何表达；
- 同一 Lot 多个 Spec 版本如何选择最新版。

这些规则必须用真实输出样例对账后冻结。

### 4. 日月新 FT 只完成了调用合同登记

本轮真实端到端验证使用的是华虹 CP。日月新 FT Cleaner 已登记并具备调用入口，但还没有完成正式结构化导入和 Golden 样例对账。

### 5. 临时输出清理机制尚未自动运行

Artifact 已记录到期时间，但自动删除到期文件的维护任务还没有实现。目前只在验证脚本中清理测试产物。

### 6. 重清洗仍有旧同步接口

旧的重清洗入口暂时保留，并做了防止提前删除旧结果的保护，但还没有改造成 A5 规划中的异步、原子切换流程。

### 7. 正式环境数据库版本仍需复验

开发库是 SQL Server 2014 SP2。本轮 Migration 和队列已经验证可运行，但正式环境仍建议升级到 SP3 后再做一次完整复验。

### 8. 前端包体积较大

前端生产构建通过，但构建工具提示部分 JavaScript Chunk 超过 500 KB。当前不影响核心功能，后续图表模块增多时需要做按页面加载和拆包。

## 四、验证结果

```text
backend unit tests=72 passed
frontend tests=13 passed
frontend production build=PASS
route_a_schema=PASS
route_a_cleaner_registry=PASS
route_a_initial_worker=PASS
route_a_worker_lease_recovery=PASS
manual_field_enrichment=PASS
empty_queue_worker=PASS
remote_sync=PASS
```

## 五、下一步做什么

下一阶段进入 A2，先完成华虹 CP 正式结构化入库：

1. 为 `CP_CSV_TRIPLET_V1` 编写 Output Adapter。
2. 从 cleaned CSV 提取 Lot、Wafer、Die、Bin、X/Y 和参数测量值。
3. 从 spec CSV 建立参数定义和 Spec Binding。
4. 从 yield CSV 对账 Lot、Wafer、Total、Pass、Fail 和 Yield。
5. 实现“相同 Spec 共用、不同 Spec 按 Lot 保存”。
6. 将数据原子写入唯一的 `test.*` Canonical 模型。
7. 成功后创建 Dataset Version 并切换 Current。
8. 用真实华虹 Golden 样例逐项对账行数、Lot、Wafer、坐标、Bin、参数、Spec 和 Yield。
9. 对账通过后，再进入缺失字段弹窗和补录应用。

## 六、后续总结报告约定

以后每个开发里程碑结束，都在 `docs/development/` 下新增一份总结报告，至少包含：

1. 本轮完成了什么；
2. 完成得比较好的地方；
3. 不确定、存在风险或完成得不够好的地方；
4. 实际验证结果；
5. 下一步开发内容。

报告中的“已完成”必须有代码、数据库结果或测试证据支持；规划中尚未实现的内容必须明确列入未完成项。
