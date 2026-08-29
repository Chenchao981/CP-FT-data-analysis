# CP/FT 数据管理与分析平台

本仓库用于规划和建设统一的 CP/FT 数据接入、清洗、质量治理、数据集发布和图表分析平台。

## 当前开发基线

当前唯一有效的业务、架构和开发入口是：

- [`docs/TMS_Development_Plan_v0.9_Production_Readiness_Closure.md`](docs/TMS_Development_Plan_v0.9_Production_Readiness_Closure.md)：当前生产就绪收口执行计划、灰度门、回归矩阵和完成定义；与 v0.7/v0.8 的计划状态或 FIRST_BATCH 规则冲突时以本计划为准。
- [`docs/business/TMS_Business_Requirements_v0.2.md`](docs/business/TMS_Business_Requirements_v0.2.md)：当前业务需求口径；与旧业务流程冲突时优先。
- [`docs/architecture/TMS_System_Architecture_v0.7_Route_A.md`](docs/architecture/TMS_System_Architecture_v0.7_Route_A.md)：Route A 系统架构与唯一 Canonical 决策。
- [`docs/business/TMS_Quick_Analysis_Business_Requirements_v0.1.md`](docs/business/TMS_Quick_Analysis_Business_Requirements_v0.1.md)：一次性快速计算与正式入库的业务边界。
- [`docs/architecture/TMS_System_Architecture_v0.8_Dual_Channel.md`](docs/architecture/TMS_System_Architecture_v0.8_Dual_Channel.md)：正式 Canonical 与临时 Workspace 双通道架构。
- [`docs/architecture/TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md`](docs/architecture/TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md)：正式入库缺 Lot 的暂停、人工确认、子 Job 重跑与审计合同。
- [`docs/development/TMS_Lot_Input_Recovery_Completion_Report_2026-08-27.md`](docs/development/TMS_Lot_Input_Recovery_Completion_Report_2026-08-27.md)：日月新/日月光真实浏览器与 SQL Server 闭环证据及剩余生产门禁。
- [`docs/development/TMS_Local_Test_Environment_Completion_Report_2026-08-28.md`](docs/development/TMS_Local_Test_Environment_Completion_Report_2026-08-28.md)：本机一键启停、常驻 Worker、UTF-8 修复和真实 FT 前端闭环证据。
- [`docs/development/TMS_v1.0_Core_Completion_Report_2026-08-29.md`](docs/development/TMS_v1.0_Core_Completion_Report_2026-08-29.md)：v1.0 Core 实现范围、确定事实、开放门和最终交付状态。
- [`docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md`](docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md)：后端、前端、Migration、真实样本、A5 和发布回归证据。
- [`docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md`](docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md)：目标 Windows Server/SQL Server 的发布、备份、恢复、计划任务和回滚入口。
- [`docs/TMS_Development_Plan_v0.8_Dual_Channel.md`](docs/TMS_Development_Plan_v0.8_Dual_Channel.md)：Quick Analysis、临时 Workspace、Storage Adapter 与 Local Agent 的历史阶段计划。
- [`docs/TMS_Development_Plan_v0.7_Route_A.md`](docs/TMS_Development_Plan_v0.7_Route_A.md)：Route A 历史阶段计划和验收拆分。
- [`docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/`](docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/README.md)：已实现技术资产和历史基线；与 v0.2/v0.7 冲突时以后者为准。
- [`docs/G0/G0_Execution_Plan_v0.6.md`](docs/G0/G0_Execution_Plan_v0.6.md)：已执行 G0 的历史计划与证据入口。

旧路线图和 v0.6 文档保留决策演进与已实现资产；如业务流程、事实源或 Cleaner 边界与 v0.2/v0.7 冲突，以 v0.2/v0.7 为准。

当前目标环境确定为远端 Windows Server 2019 + SQL Server 2014。数据库按[ADR-0001](docs/adr/ADR-0001_SQLServer2014_Target.md)采用2014兼容实现；正式开发链位于 `db/alembic/`。仓库唯一 head 和本次验收使用的 `TMS_G0_DEV` 当前均为 `sql2014_0018`；其他环境的 revision 仍必须在线确认，不能仅根据仓库文件名推断。文档包内原 `0001 → 0004` 2022+ 草案只作参考。

当前实例实测为SQL Server 2014 SP2 Enterprise（12.0.5000.0）；隔离库开发可继续，正式环境验收前需升级SP3并复验，详见[G0执行状态](docs/G0/G0_Status_2026-08-20.md)。

## 当前开发状态

- SQL Server 2014兼容Migration：仓库 head 为 `sql2014_0018`；0015～0018 分别增加原子 finalize、Worker 运维、管理/crosswalk 与 A5 生命周期；
- 隔离开发数据库：`TMS_G0_DEV` 已升级到 0018；另一个精确随机临时空库完成 0001→0018 并验证清理；
- Measurement：Rowstore聚集主键 + 普通非聚集索引；
- FastAPI后端骨架：存活检查、数据库就绪检查；
- React前端：四个固定入口、统一认证请求、Source Catalog、服务端分页/筛选、Dataset Current、Job 详情深链、分析、质量/领导视图、crosswalk、Operations 和 A5 动作；
- 华虹CP首条能力：严格DCP/TXT Parser、10套Schema、ZIP/7z安全输入、Canonical Writer和批量DQ；
- Route A Worker：Cleaner Release执行合同、SHA256校验、SQL队列租约/心跳/恢复、上传与重新处理异步化，华虹CP已写入Canonical并可直接进入数据分析；
- Lot 恢复闭环：日月新/日月光 FT 已通过真实浏览器“缺 Lot → 文件级补录 → 同 Release 子 Job → Dataset Current → 图表”验收；未知格式仍失败关闭；
- Route B空明细表已退出，`test.*`确定为唯一Canonical明细入口；
- 快速分析P0：受控服务器目录无需上传即可调用已发布杰群低内存PAT；只保存Workspace会话、Manifest和结果Artifact，不写入`test.*`；
- Dataset发布链：版本创建、输入血缘和身份门禁、阻断DQ检查、原子发布及Yield/Bin结果摘要；
- A5生命周期：最新版 Cleaner 临时导出不改变 Canonical；显式重清洗创建新版本；Owner/Admin 逻辑归档保留 Source/Batch/`test.*` 并退出 Current View；
- 管理与主数据：领导/质量 KPI 显示时间范围、样本数和未知占比；源产品先进入 PENDING crosswalk，不自动升级为 SAP-B1 企业物料；
- 运维：Worker registry/heartbeat/drain，Quick/Formal 两套独立 DryRun 清理，四个 Windows 计划任务以及备份/恢复/可复现发布工具；
- 真实SQL Server集成：Canonical写入、DQ Gate、Dataset发布、结果查询及测试数据清理通过；
- 开发运行说明：[backend/README.md](backend/README.md)。
- 前端运行说明：[frontend/README.md](frontend/README.md)。
- 华虹格式证据：[docs/formats/huahong/README.md](docs/formats/huahong/README.md)。
- Route A开发状态：[docs/development/TMS_Route_A_Development_Status_2026-08-24.md](docs/development/TMS_Route_A_Development_Status_2026-08-24.md)。
- Quick Workspace生命周期验收：[docs/development/TMS_Quick_Workspace_Lifecycle_Q0_1_Completion_Report_2026-08-27.md](docs/development/TMS_Quick_Workspace_Lifecycle_Q0_1_Completion_Report_2026-08-27.md)。

开发库当前保留 139 个 Test Run、291,127 个 Unit Result、5,578,114 个 Measurement 和 10 个 Published Current Dataset Version。最终全量测试数、提交号和发布 ZIP SHA 以[回归测试报告](docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md)最后回填为准。

> **交付边界**：当前工作是仓库实现和开发库 G0-G2 收口。G3 测试服务器与 G4 生产分批尚未执行；SQL Server SP3、HTTPS、正式服务账号、备份恢复、业务 UAT 和签字完成前，不得表述为“生产已上线”。

## 本机一键测试

当前开发机可直接双击仓库根目录的：

- [`启动TMS测试环境.bat`](启动TMS测试环境.bat)：后台启动 SQL API、Route A Worker 和前端，并打开浏览器；
- [`查看TMS测试环境状态.bat`](查看TMS测试环境状态.bat)：确认 API、Worker、前端是否全部就绪；
- [`停止TMS测试环境.bat`](停止TMS测试环境.bat)：先停止前端，等待 Worker 当前任务完成后退出，再停止 API。

该入口只监听 `127.0.0.1`，默认关闭认证以便本机功能验收，不是生产部署方式。完整操作、缺 Lot 补录、分析入口和报障信息见 [`TMS 本机测试使用指南`](docs/development/TMS_Local_Test_User_Guide_2026-08-28.md)。

## Route A 产品主线

```text
原始文件 / 压缩包
→ 调用已发布的原 Python Cleaner
→ 临时 RawData / Spec / Statistics 三个 Excel
→ 基础校验并自动写入唯一 Canonical
→ 数据库历史查询 / 良率与 Bin / 参数与空间图表
→ 最新 Cleaner 临时导出，或显式重清洗原子更新
```

厂家和格式差异保留在原 CP/FT Cleaner。最终用户面对上传任务、正式结构化数据、补录、分析图表和临时下载，不需要执行人工 Dataset 审核发布。

## 快速分析产品主线

```text
管理员配置的服务器目录
→ 用户只选择数据源代码与相对目录
→ SQL队列 QUICK_PAT
→ 调用已发布 FT 工具包
→ PAT Excel + 来源Manifest + 统计摘要（带TTL）
```

快速分析用于一次性PAT等需求，不上传原始文件，也不创建正式Measurement。需要长期追溯、跨批次比较或正式报表时仍走Route A正式入库。

## 参考项目边界

本地 `历史项目-参考用/` 包含 CP、FT 和 VDMOS 历史项目、样例和输出，只用于事实核对，不进入 Git。任何清洗、规格、Bin 或统计规则必须经过样例验证和业务批准后，才能进入本平台的版本化规则。

## 数据安全

仓库禁止提交原始测试数据、客户或产品样例、上传目录、输出报表、数据库、密钥、日志、构建包和本地环境配置。提交前必须检查暂存文件清单和大文件。
