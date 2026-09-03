# TMS 能力分类基线 v1.0（2026-08-27）

## 1. 决策结论

TMS 从本版本开始将能力分为三条独立通道，页面、接口、Cleaner Release 和验收报告都必须显式标注所属通道：

1. **通用正式入库**：面向持续发生的 CP/FT 生产数据，完成厂家格式校验、清洗、Canonical 入库、Dataset 发布和分析。
2. **定制工具**：面向特定小组、特定 Excel 任务的低频功能，保留独立入口和独立输出，不作为通用 CP Die 明细能力宣传，也不默认写入正式数据集。
3. **快速分析**：面向一次性大数据统计，使用临时 Workspace 和 TTL，不要求先形成全量 Measurement 正式数据。

三个通道可以复用同一份已发布 Cleaner 代码，但不得通过修改名称把定制功能伪装成通用功能，也不得把临时分析结果混入正式 Canonical 表。

## 2. 当前厂家能力清单

| 方向 | 厂家/功能 | 分类 | 现有 Cleaner | TMS 当前状态 | 本阶段处理 |
|---|---|---|---|---|---|
| CP | 华虹 | 通用正式入库 | 已有 | 已开放 | 保持 |
| CP | Jetech | 通用正式入库 | 已有 | 已开放 | 保持 |
| CP | 立昂微 | 通用正式入库 | 已有 | 已开放 | 保持 |
| CP | 国宇 FRD Excel 清洗 | 定制工具 | 已有 | 历史正式数据和 Release 保留；不再出现在通用 CP 新建/重跑入口 | 移至定制工具说明页 |
| CP | 立昂微-管芯数 | 定制工具 | 已有 | 使用原独立桌面工具 | 移至定制工具说明页 |
| FT | 日月新 | 通用正式入库 | 已有 | DC 已开放 | 收紧真实格式和身份校验 |
| FT | 日月光 | 通用正式入库 | 旧代码曾与日月新/ASE 混用 | DC 本阶段独立开放 | 建立独立 Factory、Adapter、Supplier 和 Spec 命名空间 |
| FT | 电基 | 通用正式入库 | 已有 | Cleaner 可用，正式 Route A 尚未接入 | 后续独立验收后开放 |
| FT | 集佳 | 通用正式入库 | 已有 | Cleaner 可用，正式 Route A 尚未接入 | 后续独立验收后开放 |
| FT | 杰群 | 通用正式入库/快速分析 | 已有 | Quick PAT 已开放，正式 Route A 尚未接入 | 两条通道分别验收 |

“已有 Cleaner”只表示旧项目对已批准格式能够清洗，不等于 TMS 正式入库链路已经完成。正式入口只开放通过 Factory + Format Profile + Release + Output Adapter + SQL 对账的组合。

## 3. Lot 与 Spec 规则

1. 当前已批准的 CP 厂家（华虹、Jetech、立昂微）和 FT 厂家（日月新、日月光、电基、集佳、杰群）的现有格式均能提取 Lot；新厂家必须按真实样本增加专用规则。
2. Lot 提取失败属于异常容错场景。Cleaner 必须停止正式发布，并要求用户补录 Lot；不能用目录名、文件全名或 `unknown` 伪造业务批次。
3. Spec 绑定粒度是 **Lot**。同一 Lot 内规格指纹一致时可以共享；不同 Lot 或同 Lot 规格不一致时必须隔离或停止，不能默认采用第一个文件/第一个批次的 Spec。
4. 用户补录必须保留操作者、时间、原因和源文件范围；补录完成后才允许重新清洗或发布。

## 4. 日月新与日月光身份边界

| 项目 | 日月新（RIYUEXIN） | 日月光（RIYUEGUANG/ASE） |
|---|---|---|
| 业务身份 | 独立封测厂 | 独立封测厂 |
| 当前正式范围 | FT DC XLSX | FT DC XLSX |
| 已验证文件名 | `设备_产品_Lot_日期_时间.xlsx`；以及 `产品_Lot_设备_DC_时间.xlsx` | `设备_产品_Lot_日期_时间.xlsx` |
| 已验证头部 | Item 第 2 行、Unit 第 7 行、Test No. 第 19 行 | Item 第 2 行、Time 第 7 行、Unit 第 8 行、Test No. 第 15 行 |
| 数据库 Supplier | `RIYUEXIN` / 日月新 | `RIYUEGUANG` / 日月光 |
| 兼容别名 | 日月新 | 日月光、ASE |

即使两类文件都来自相近设备体系，也不得再使用 `ASE -> RIYUEXIN` 的业务别名。未知文件名方向、未知 Unit 行、混入 DVDS/RG/HTDC/TF 的目录必须失败关闭。

## 5. 页面与接口约束

1. 工程/量产 CP 的厂家选择只显示华虹、Jetech、立昂微。
2. 工程/量产 FT 的正式选择只启用日月新和日月光；电基、集佳、杰群在能力页显示当前接入状态，但未验收前不能提交正式入库任务。
3. 单独提供“定制工具”页面，展示国宇 FRD、立昂微-管芯数的用途、边界和当前运行方式。
4. 历史国宇数据、Cleaner 代码和 Release 不删除；通用上传与重新处理接口不再接受国宇。
5. 前后端都使用独立 `riyuexin` / `riyueguang` 代码；`ase` 只映射到 `riyueguang`。

## 6. 本基线边界与后续增量

- 本阶段不宣称日月光 DVDS、RG、HTDC、TF 已接入正式数据库。
- 本阶段不宣称电基、集佳、杰群正式 Route A 已完成。
- **同日增量更新（取代本文件初版结论）**：Lot 人工补录闭环已经实现。当前正式开放的 FT Cleaner 在无法取得 Lot 时必须进入 `NEEDS_INPUT`，用户按源文件补录后形成审计记录并恢复原 Cleaner Release；成功重跑后才能发布 Dataset Current。实现与验收边界分别见 `docs/architecture/TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md` 和归档的 `docs/archive/2026-08-24_to_2026-09-03_delivery-records/development/TMS_Lot_Input_Recovery_Completion_Report_2026-08-27.md`。
- 本阶段不删除或迁移已有历史正式数据。
