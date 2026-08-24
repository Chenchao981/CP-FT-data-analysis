# CP/FT 数据管理与分析平台

本仓库用于规划和建设统一的 CP/FT 数据接入、清洗、质量治理、数据集发布和图表分析平台。

## 当前开发基线

当前唯一有效的开发入口是：

- [`docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/`](docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/README.md)
- [`docs/TMS_Implementation_Roadmap_v0.6.md`](docs/TMS_Implementation_Roadmap_v0.6.md)
- [`docs/G0/G0_Execution_Plan_v0.6.md`](docs/G0/G0_Execution_Plan_v0.6.md)
- [`docs/business/TMS_Business_Requirements_v0.2.md`](docs/business/TMS_Business_Requirements_v0.2.md)：当前业务需求口径；与旧业务流程冲突时优先。

本机的 `docs/0.1`、v0.2、v0.3 和 v0.5 目录只保留决策演进记录，不进入 GitHub；如旧文档与 v0.6 冲突，以 v0.6 为准。

当前目标环境确定为远端 Windows Server 2019 + SQL Server 2014。数据库按[ADR-0001](docs/adr/ADR-0001_SQLServer2014_Target.md)采用2014兼容实现；正式开发链位于 `db/alembic/`，开发库已升级到 `sql2014_0008`。文档包内原 `0001 → 0004` 2022+草案只作参考。

当前实例实测为SQL Server 2014 SP2 Enterprise（12.0.5000.0）；隔离库开发可继续，正式环境验收前需升级SP3并复验，详见[G0执行状态](docs/G0/G0_Status_2026-08-20.md)。

## 当前开发状态

- SQL Server 2014兼容Migration：`sql2014_0001 → sql2014_0006`；
- 隔离开发数据库：`TMS_G0_DEV`，空库升级与Schema验证通过；
- Measurement：Rowstore聚集主键 + 普通非聚集索引；
- FastAPI后端骨架：存活检查、数据库就绪检查；
- React前端：清洗任务、华虹样本检查、Dataset结果审核与发布；
- 华虹CP首条能力：严格DCP/TXT Parser、10套Schema、ZIP/7z安全输入、Canonical Writer和批量DQ；
- Dataset发布链：版本创建、输入血缘和身份门禁、阻断DQ检查、原子发布及Yield/Bin结果摘要；
- 真实SQL Server集成：Canonical写入、DQ Gate、Dataset发布、结果查询及测试数据清理通过；
- 开发运行说明：[backend/README.md](backend/README.md)。
- 前端运行说明：[frontend/README.md](frontend/README.md)。
- 华虹格式证据：[docs/formats/huahong/README.md](docs/formats/huahong/README.md)。

## 产品主线

```text
原始文件 / 压缩包
→ Format Profile + Cleaner Release
→ Processing Run + Data Quality Gate
→ Published Dataset Version
→ Evaluation Run
→ 清洗结果 / 良率与 Bin / 参数与空间图表
→ Export Job / Report
```

厂家和格式差异只存在于接入与清洗层。最终用户统一面对任务、数据质量、已发布数据集、分析图表和交付物。

## 参考项目边界

本地 `历史项目-参考用/` 包含 CP、FT 和 VDMOS 历史项目、样例和输出，只用于事实核对，不进入 Git。任何清洗、规格、Bin 或统计规则必须经过样例验证和业务批准后，才能进入本平台的版本化规则。

## 数据安全

仓库禁止提交原始测试数据、客户或产品样例、上传目录、输出报表、数据库、密钥、日志、构建包和本地环境配置。提交前必须检查暂存文件清单和大文件。
