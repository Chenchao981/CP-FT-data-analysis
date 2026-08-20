# TMS 实施路线图 v0.6

> 编制日期：2026-08-20  
> 依据：`TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics`  
> 状态：候选执行规划  
> 原则：先建立可审计的数据闭环，再扩展厂家格式和图表数量；未知格式、身份、单位、Spec、Bin 或规则版本一律失败关闭。

## 1. 建设目标

交付一个面向 CP/FT 最终用户的统一平台：厂家差异由 Format Profile 和 Cleaner Release 消化，用户通过 Dataset Version 使用经过 DQ 验收的清洗结果，并在固定数据、筛选和规则版本下复算 Yield、Bin、Spec、PAT、SBL、Cpk 和 SPC。

首个可验收版本必须完成两条真实 Vertical Slice：

1. 华虹 CP：输入集合 → 清洗/DQ → 数据集发布 → Lot/Wafer → Yield/Bin → Wafer Map/参数图 → 明细与导出。
2. 日月新 FT：输入集合 → 清洗/DQ → 数据集发布 → Product/Lot → PASS/FAIL/Bin → 参数/PAT 图 → 明细与导出。

## 2. 项目阶段

| 阶段 | 建议周期 | 主要交付 | 退出条件 |
|---|---:|---|---|
| G0 基线验收 | 1～2 周 | 业务决策、黄金样例、SQL Server 环境、安全边界、架构 ADR | 门禁事项有负责人、证据和批准结论 |
| P0 工程骨架 | 2 周 | Backend/Frontend/Worker/DB/Test 骨架、CI、配置、日志、认证最小闭环 | 空环境可部署，Migration 和鉴权 Smoke Test 通过 |
| P1 华虹 CP Slice | 3～4 周 | HH Format Profile/Cleaner、DQ、Dataset、CP 查询与核心图表 | 黄金样例行数、身份、Bin、Yield、Spec 和坐标 100% 对账 |
| P2 日月新 FT Slice | 3～4 周 | ASE Format Profile/Cleaner、DQ、Dataset、FT 查询、PAT/散点 | Unit、参数、单位、PASS/FAIL、Bin 与异常状态 100% 对账 |
| P3 通用分析与交付 | 2～3 周 | BoxPlot、Histogram、Scatter、Cpk、PAT/SBL/SPC、Saved Analysis、Export Job | 图表/明细/导出使用同一 Dataset、Filter 和 Rule Context |
| P4 多厂家扩展 | 按格式滚动 | JT、Lion、国宇、杰群、电基、集佳逐格式接入 | 每个格式独立 Profile、黄金样例、回归测试和发布批准 |
| P5 生产硬化 | 2～3 周 | 性能、备份恢复、审计、监控、部署、UAT、运维和用户手册 | SLA、恢复演练、安全审计和 UAT 全部通过 |

周期是假设 3～4 人并行且业务问题能及时确认的初始区间；若单人串行开发，应按完成的 Vertical Slice 重新排期，不把日历日期当作验收证据。

## 3. G0 必须关闭的决策

| 编号 | 决策 | 推荐默认值 | 必要证据 |
|---|---|---|---|
| G0-01 | 首批真实样例 | 华虹 CP、日月新 FT各覆盖正常、Fail、Retest、异常格式 | 样例台账、SHA256、脱敏/存储批准 |
| G0-02 | 黄金结果 | 每种格式固定行数、Unit/Die、参数、Bin、Yield、状态计数 | 人工确认的 Golden Manifest |
| G0-03 | SQL Server | DEV/TEST 用 SQL Server 2022 Developer；生产 Edition 后续批准 | 实例、Collation、容量、备份路径 |
| G0-04 | 原始文件存储 | 优先复用受控 NAS；数据库只存 URI、Hash 和元数据 | 权限、保留期限、恢复测试 |
| G0-05 | 身份与权限 | 优先 AD/OIDC；权限按 Role + Data Scope + Object Authorization | 角色矩阵、测试账号、越权用例 |
| G0-06 | 数据权限维度 | 首版建议 Product/Project + Owner，部门作为补充范围 | 跨部门、跨项目和导出场景确认 |
| G0-07 | Retest | 同时支持 First Pass、Final Result、All Attempts；页面显示当前口径 | CP/FT 各一组人工复算样例 |
| G0-08 | Spec/Bin | 明确优先级、唯一匹配和无匹配/多匹配阻断 | 多 Lot、多产品、多程序冲突用例 |
| G0-09 | 统计规则 | PAT/SBL/Cpk/SPC 各指定业务 Owner 和批准版本 | 算法说明、测试向量、批准记录 |
| G0-10 | 保留与恢复 | 原始文件、数据集、评价结果、导出物分别定期限 | RPO/RTO、备份与恢复演练方案 |

## 4. P0 工程结构

```text
backend/
  api/ domain/ application/ infrastructure/ workers/
frontend/
  app/ features/ components/ api/ charts/
db/
  alembic/ seeds/ smoke_tests/
tests/
  unit/ contract/ integration/ golden/ security/
docs/
  adr/ formats/ operations/ user-guide/
```

P0 不搬运历史项目代码。先定义 Detector、Cleaner、Canonical Writer、DQ、Dataset Publisher、Evaluation 和 Export 的稳定接口，再按已批准格式逐项迁移。

## 5. 每个厂家格式的标准接入流程

```text
真实样例 Profile
→ 文件角色与格式签名确认
→ 字段/身份/单位/Spec/Bin 映射批准
→ Format Profile 发布
→ Cleaner 实现与版本登记
→ Golden Test + 相似未知格式拒绝测试
→ Dataset 对账
→ GUI/API/图表联调
→ 业务验收与 Cleaner Release 发布
```

不得通过放宽既有 Parser 来兼容新变体；不同结构或单位合同必须形成新 Profile Version。

## 6. 分层验收矩阵

### 接入与清洗

- 文件 SHA、角色、顺序、Format Profile、Cleaner Release 可追溯；
- 源行到 Run、Unit/Die、Measurement 可追溯；
- Lot/Wafer/Test Lot/Product/Program 不猜测；
- 单位未知、格式歧义、混合版本、重复关键身份必须阻断；
- 源行、输出行、Unit、Measurement、Bin 和 Yield 对账。

### 数据集与规则

- 只有通过 DQ Gate 的 Dataset Version 可发布；
- BLOCKER 永不允许 Waive；
- 多 Lot CP 按每行 Lot 匹配规格，不取第一份或最新一份；
- Evaluation Run 固定 Dataset Version、Rule Version、Filter Hash 和排除记录；
- Retest 的统一排序键及统计口径有测试向量。

### 用户界面与图表

- Lot/Wafer/Parameter 默认全选并支持单选、多选；
- 图表、明细和导出共享同一规范化筛选；
- 前端不硬编码 PASS Bin，不静默删除 IQR 异常，不执行权威单位换算；
- Wafer Map 只有在 Lot + Wafer + X + Y 可信时开放；
- 每个结论可钻取到 Measurement、规则和输入文件。

### 安全与运维

- API 每次执行对象级授权，下载链接短时有效；
- 管理员不因系统权限自动获得业务数据导出权；
- 原始数据、密钥、日志和生成报表不进入 Git 或应用包；
- Worker 重启、重复提交、取消、超时和重跑可恢复；
- SQL Server 完成备份、Restore Drill 和容量压测。

## 7. 首个两周执行清单

1. 评审并批准 v0.6 架构、用户流程和本路线图。
2. 确定 G0-01～G0-10 的 Owner、截止日期和证据位置。
3. 建立只存元数据的样例台账，真实数据放受控数据区。
4. 准备 SQL Server 2022 DEV/TEST，执行 `0001 → 0004` Migration。
5. 补充历史 `measurement_evaluation` 回填方案和后续 `NOT NULL` revision。
6. 建立后端、前端、Worker、测试和 CI 工程骨架。
7. 固化 Format Detector/Cleaner/DQ/Dataset/Evaluation/Export 接口。
8. 为华虹 CP 和日月新 FT 建立第一版 Golden Manifest。
9. 完成登录、数据范围和对象级授权的最小安全闭环。
10. 第 10 个工作日进行 G0/P0 Gate Review，只在通过后启动华虹 CP Slice。

## 8. 版本与变更治理

- v0.6 是当前开发基线；旧版本只用于审计决策演进。
- Schema 只能通过 Alembic revision 变更；共享环境使用过的 revision 不改写。
- 业务口径以 ADR 记录问题、样例证据、决定、影响、Owner 和批准日期。
- 每个阶段按可运行 Vertical Slice 验收，不按代码完成百分比验收。
- 新格式、新规则和新导出模板都必须独立版本化并可追溯。
