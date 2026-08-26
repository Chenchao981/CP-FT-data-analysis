# CP/FT 数据管理与分析平台

本仓库用于规划和建设统一的 CP/FT 数据接入、清洗、质量治理、数据集发布和图表分析平台。

## 当前开发基线

当前唯一有效的业务、架构和开发入口是：

- [`docs/business/TMS_Business_Requirements_v0.2.md`](docs/business/TMS_Business_Requirements_v0.2.md)：当前业务需求口径；与旧业务流程冲突时优先。
- [`docs/architecture/TMS_System_Architecture_v0.7_Route_A.md`](docs/architecture/TMS_System_Architecture_v0.7_Route_A.md)：Route A 系统架构与唯一 Canonical 决策。
- [`docs/business/TMS_Quick_Analysis_Business_Requirements_v0.1.md`](docs/business/TMS_Quick_Analysis_Business_Requirements_v0.1.md)：一次性快速计算与正式入库的业务边界。
- [`docs/architecture/TMS_System_Architecture_v0.8_Dual_Channel.md`](docs/architecture/TMS_System_Architecture_v0.8_Dual_Channel.md)：正式 Canonical 与临时 Workspace 双通道架构。
- [`docs/TMS_Development_Plan_v0.8_Dual_Channel.md`](docs/TMS_Development_Plan_v0.8_Dual_Channel.md)：Quick Analysis、临时 Workspace、Storage Adapter 与 Local Agent 的阶段计划。
- [`docs/TMS_Development_Plan_v0.7_Route_A.md`](docs/TMS_Development_Plan_v0.7_Route_A.md)：后续开发阶段、交付物和验收门槛。
- [`docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/`](docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/README.md)：已实现技术资产和历史基线；与 v0.2/v0.7 冲突时以后者为准。
- [`docs/G0/G0_Execution_Plan_v0.6.md`](docs/G0/G0_Execution_Plan_v0.6.md)：已执行 G0 的历史计划与证据入口。

旧路线图和 v0.6 文档保留决策演进与已实现资产；如业务流程、事实源或 Cleaner 边界与 v0.2/v0.7 冲突，以 v0.2/v0.7 为准。

当前目标环境确定为远端 Windows Server 2019 + SQL Server 2014。数据库按[ADR-0001](docs/adr/ADR-0001_SQLServer2014_Target.md)采用2014兼容实现；正式开发链位于 `db/alembic/`。当前代码已包含 `sql2014_0009`；实际数据库 revision 必须在 A0 只读盘点中确认，不能仅根据仓库文件名推断。文档包内原 `0001 → 0004` 2022+草案只作参考。

当前实例实测为SQL Server 2014 SP2 Enterprise（12.0.5000.0）；隔离库开发可继续，正式环境验收前需升级SP3并复验，详见[G0执行状态](docs/G0/G0_Status_2026-08-20.md)。

## 当前开发状态

- SQL Server 2014兼容Migration：仓库 head 为 `sql2014_0012`；开发库在线升级等待数据库网络恢复后复验；
- 隔离开发数据库：`TMS_G0_DEV`，空库升级与Schema验证通过；
- Measurement：Rowstore聚集主键 + 普通非聚集索引；
- FastAPI后端骨架：存活检查、数据库就绪检查；
- React前端：清洗任务、华虹样本检查、Dataset结果审核与发布；
- 华虹CP首条能力：严格DCP/TXT Parser、10套Schema、ZIP/7z安全输入、Canonical Writer和批量DQ；
- Route A Worker：Cleaner Release执行合同、SHA256校验、SQL队列租约/心跳/恢复、上传与重新处理异步化，华虹CP已写入Canonical并可直接进入数据分析；
- Route B空明细表已退出，`test.*`确定为唯一Canonical明细入口；
- 快速分析P0：受控服务器目录无需上传即可调用已发布杰群低内存PAT；只保存Workspace会话、Manifest和结果Artifact，不写入`test.*`；
- Dataset发布链：版本创建、输入血缘和身份门禁、阻断DQ检查、原子发布及Yield/Bin结果摘要；
- 真实SQL Server集成：Canonical写入、DQ Gate、Dataset发布、结果查询及测试数据清理通过；
- 开发运行说明：[backend/README.md](backend/README.md)。
- 前端运行说明：[frontend/README.md](frontend/README.md)。
- 华虹格式证据：[docs/formats/huahong/README.md](docs/formats/huahong/README.md)。
- Route A开发状态：[docs/development/TMS_Route_A_Development_Status_2026-08-24.md](docs/development/TMS_Route_A_Development_Status_2026-08-24.md)。

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
