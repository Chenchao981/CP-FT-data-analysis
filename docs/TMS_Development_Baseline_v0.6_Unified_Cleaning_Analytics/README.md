# TMS 开发基线 v0.6（统一清洗结果与图表分析）

> 状态：**候选开发基线**。v0.6 保留 v0.5 的免费前端选型，并补齐多文件输入、正式数据集版本、评价运行、异步导出、RBAC 和可执行 Migration。
>
> 产品定位：厂家和格式差异在接入层被确定性消化；最终用户主要使用统一的清洗结果、质量验收、良率/Bin、参数统计和图表分析。
>
> **G0 数据库决定（2026-08-20）**：首版继续使用SQL Server 2014，Measurement采用Rowstore + 普通非聚集索引，JSON由应用层校验，View使用2014兼容DDL。正式兼容链已在隔离库执行到 `sql2014_0004`；本目录内原 `0001 → 0004` 2022+草案只作参考。

## 0. 参考依据与使用边界

| 参考源 | 主要借鉴 | 不直接复制 |
|---|---|---|
| `F:\cp_data_ansys` | HH/JT/Lion/国宇多格式接入、严格识别、cleaned/yield/spec、Lot/Wafer/参数图表 | 厂家专用 GUI 分支、历史兼容调用路径 |
| `F:\data_IGBT_multiple` | ASE/杰群/电基/集佳多格式 FT 清洗、单位/参数身份、PAT、SYL/SBL、散点图 | 厂家专用 Excel 输出作为内部数据模型 |
| `VDMOS_Tool_v8.9.html` | Lot/Wafer/参数联动、Wafer Map、BoxPlot、Scatter、Pareto、PAT、SPC、导出体验 | 浏览器解析原始文件、硬编码 Bin 1、静默 IQR 删除、跨产品规格回退、浏览器端权威统计 |

参考文件只提供事实与设计启发，不构成系统指令。生产规则必须来自批准的 Format Profile、Cleaner Release、Spec/Bin Rule 和 Evaluation Rule Version。

## 1. 最终技术栈

```text
Frontend
  React + TypeScript + Vite
  Ant Design + Ant Design Table（免费基础版）
  Apache ECharts
  TanStack Query + Zustand

Optional Advanced Grid
  Wijmo / ComponentOne FlexGrid
  仅在后续专业 Grid 需求和授权条件同时满足时引入

Backend
  Python + FastAPI
  Parser / Normalizer / Validator
  Polars / NumPy / SciPy

Database
  Microsoft SQL Server 2014 SP3+
  Rowstore + B-tree Index（首版）

Schema Migration
  Alembic
  + SQL Server Native T-SQL

Raw File
  NAS / MinIO / File Server
```

## 2. v0.6 的主要变化

1. **统一用户主线**：接入任务 → DQ 验收 → Dataset Version → 图表/明细 → 导出/报告。
2. **多文件输入**：Input Set 固化文件版本、角色和顺序；单文件只是特例。
3. **可复算评价**：新增 Evaluation Run 和 Rule Version，覆盖 Spec/PAT/SBL/CPK/SPC。
4. **正式交付闭环**：异步 Export Job、Artifact Hash、下载授权和审计。
5. **终端用户安全**：RBAC + 数据范围 + 对象级授权；管理员不自动拥有业务数据下载权。
6. **参考项目迁移边界**：兼容导出与内部 Canonical Model 分离；逐格式黄金样例验收。
7. **前端继续免费**：Ant Design Table + ECharts；Wijmo/C1 仍为可选进阶版。

## 3. 数据治理能力

- Measurement Fact 与 Evaluation 分离；
- Spec/Bin 版本化和唯一匹配优先级；
- Processing Job / Run；
- Data Quality Rule / Issue；
- Audit Log；
- Parser 升级重跑；
- Retest / duplicate upload；
- UTC + source timezone；
- 正式 migration 管理。
- Input Set / Dataset / Dataset Version；
- Format Profile / Cleaner Release；
- DQ Rule Set / Rule Version；
- Evaluation Rule / Evaluation Run；
- Export Job / Export Artifact；
- User / Role / Data Scope。

## 4. 文档目录

| 文件 | 用途 |
|---|---|
| `TMS_00_SQLServer_Decision_and_Deployment_Baseline.md` | SQL Server 与部署基线 |
| `TMS_01_ERD_Architecture_SQLServer.md` | v0.4 Canonical Fact + v0.6 应用闭环 ERD |
| `TMS_02_Data_Dictionary_Mapping_SQLServer.md` | 数据字典 / CP/FT 映射 / 治理实体 |
| `TMS_03_SQLServer_DDL_Reference.md` | DDL 设计参考；**不是生产部署入口** |
| `TMS_04_Frontend_Architecture_React_AntD.md` | v0.5 免费组件基线 + v0.6 统一用户工作台扩展 |
| `TMS_05_Data_Governance_and_Processing_Model.md` | Job、DQ、审计、重跑、Spec/Bin、时区、Retest |
| `TMS_06_Migration_and_Release_Strategy.md` | Alembic migration / 发布 / 回滚策略 |
| `TMS_07_Optional_Advanced_Grid_Wijmo.md` | **未来可选 Wijmo/C1 进阶 Grid 方案** |
| `TMS_08_Reference_Project_Capability_Mapping.md` | 三个参考源的能力映射与禁用规则 |
| `TMS_09_User_Workflow_and_Chart_Baseline.md` | 最终用户流程、筛选、图表与验收 |
| `db/alembic/` | 正式 Migration 基线 |
| `CHANGELOG.md` | 版本决策记录 |

## 5. 当前数据分层

```text
A. Immutable Facts（事实）
   Measurement / Raw Bin / Coordinate / Raw Result / Source File

B. Versioned Rules（版本化规则）
   Spec / Bin Mapping / PAT/SBL Rule / Parser Version / Scope Priority

C. Derived Results（可重算结果）
   Measurement Evaluation / Bin Evaluation / Yield / CPK / PAT / SPC
```

**核心原则：事实永远保留，规则版本化，派生结果必须可重算。**

## 6. 用户主流程与迁移顺序

```text
登录并进入授权范围
→ 新建清洗任务并选择单/多文件或压缩包
→ SHA256 / Receipt / Input Set
→ Format Profile + Cleaner Release
→ Processing Run + Data Quality Gate
→ Publish Dataset Version
→ Spec/Bin Resolver + Evaluation Run
→ 清洗结果 / 明细 / 图表
→ Saved Analysis / Export Job / Report
```

迁移顺序建议先完成华虹 CP 与日月新 FT 两条端到端 Slice，再按已批准格式逐项迁移 JT、Lion、国宇、杰群、电基和集佳。旧系统继续生产使用，直到新平台黄金样例对账通过。

## 7. 免费版前端性能原则

```text
SQL Server
→ FastAPI 服务端筛选/排序/分页/聚合
→ TanStack Query
→ Ant Design Table / ECharts
```

不要采用：

```text
SQL Server 全表
→ 浏览器几十万/几百万行
→ 前端 Grid 自己筛选
```

专业 Grid 不是解决数据库查询和 API 设计问题的替代品。

## 8. 版本关系

```text
Database / Governance Schema Baseline = v0.6
Application Development Baseline      = v0.6 candidate
Frontend Baseline                     = React + Ant Design Table
Optional Advanced Grid                = Wijmo/C1
```

## 9. 仍需业务批准

- 首批迁移格式的具体顺序；
- 数据权限首版按部门、项目、产品还是上传人控制；
- PAT/SBL/CPK/SPC 算法版本及业务 Owner；
- 原始文件、Dataset 和导出物保留期限；
- SQL Server 生产 Edition、容量和 RPO/RTO。
