# TMS 系统架构 v0.7（Route A：调用既有 Python Cleaner）

状态：**开发架构候选，供评审后执行**

形成日期：2026-08-24

业务依据：`docs/business/TMS_Business_Requirements_v0.2.md`

## 1. 结论先行

首版采用**模块化单体 + 独立 Worker + SQL Server 数据库队列**。现有 CP Cleaner 与 FT Cleaner 继续作为两个独立程序运行；TMS 不统一或重写厂家解析、单位转换、Bin、参数拆解或统计逻辑，只负责从四个固定业务入口调度对应 Cleaner、校验其版本化输出、写入正式结构化存储，以及提供补录、查询、图表、导出、重清洗和删除。

数据库只保留一套明细事实源：

```text
ingestion.processing_run
  → test.test_run
  → test.unit_result
  → test.measurement
  → dataset.dataset_version（内部原子切换机制）
```

`analysis.run / analysis.unit / analysis.test_item / analysis.measurement` 不再作为 Route A 的入库目标，也不再扩展。它与 `test.*` 表达了同一批测试事实，却缺少原始值、测量状态、处理血缘和完整规格关系；继续并存会导致查询、补录、图表和重清洗分别读到不同“真相”。后续只通过新的 Alembic revision 停用或删除，绝不改写已经执行过的 `sql2014_0009`。

## 2. 架构目标与约束

### 2.1 必须满足

- 最大约 8 人并发、通常 2～3 人在线，不为假设中的大规模并发引入微服务或消息中间件；
- CP 与 FT 分别调用原有独立 Cleaner，不合并程序和清洗逻辑；
- 工程-CP、工程-FT、量产-CP、量产-FT 是四个固定入口，入口直接确定业务域和测试阶段，不再自动识别；
- Cleaner 成功且基础校验通过后自动成为正式数据，不设置人工审核/发布页面；
- 一个任务支持多个 Lot；每条 Unit 保留 Cleaner 给出的 Lot_ID；
- Product、Lot_ID 等允许为空并可任务级补录，补录不覆盖 Cleaner 原始值；
- 普通用户严格按上传任务 Owner 隔离，管理员可操作全部任务；
- 默认二次导出使用最新版 Cleaner，但不更新数据库；
- 显式“重新清洗并更新数据”必须原子替换，失败时旧数据继续可用；
- TMS 删除不删除 FTP 原始文件。

### 2.2 首版不建设

- 微服务、Kafka、RabbitMQ、Redis 队列或 Kubernetes；
- 通用未知格式自动识别平台；
- TMS 内部重写 CP/FT 清洗器；
- 用户可见的复杂 Dataset 审核、发布和版本管理；
- 删除审批、删除后的长期业务审计；
- 浏览器加载全量 Measurement 后自行做权威筛选和统计。

## 3. 总体组件

架构图源文件：`docs/architecture/TMS_System_Architecture_v0.7_Route_A.drawio`。

```text
React 用户界面
  │ HTTPS / JSON
FastAPI 模块化单体
  ├─ 身份与 Owner/Admin 授权
  ├─ 上传任务、补录、查询、图表、下载、删除 API
  ├─ Cleaner Registry / 运行合同
  └─ SQL Server Job Queue 写入端
       │
独立 Python Worker（默认 1 个，可配置为 2 个）
  ├─ 从数据库领取带租约的任务
  ├─ 从 FTP/受控存储取得原始输入
  ├─ 在每次运行独立临时目录调用已发布 Cleaner
  ├─ 校验 RawData / Spec / Statistics 三个 Excel
  └─ 通过 Canonical Importer 单事务/分批写入 SQL Server

长期存储：FTP 原始文件 + SQL Server 结构化数据
临时存储：Cleaner 输出和下载包，按 TTL 清理
```

### 3.1 为什么是这个形态

| 决定 | 原因 |
|---|---|
| FastAPI 模块化单体 | 当前团队和并发不需要服务拆分；事务、权限和发布更容易保持一致 |
| Worker 独立进程 | Cleaner 耗时且依赖独立，不能阻塞 Web 请求，也要隔离 CP/FT 包冲突 |
| SQL Server 数据库队列 | 已有 `ingestion.processing_job`，并发小；用行锁、租约和心跳即可可靠领取 |
| 原始文件留 FTP | 符合现有归档边界，数据库只保存 URI、SHA256、文件角色与回执 |
| 临时 Excel 不长期保存 | 数据库是长期主体；只有下载任务在 TTL 内保留临时产物 |
| 内部 Dataset Version | 只作为重清洗原子切换和失败保护，不暴露人工发布流程 |

## 4. 模块边界

### 4.1 前端 React

- 登录与用户/管理员菜单；
- 工程-CP、工程-FT、量产-CP、量产-FT 四个独立上传页；
- 任务列表、状态、Cleaner 版本、缺失字段能力提示；
- 任务补录、历史查询、明细和图表；
- “使用最新版 Cleaner 生成下载文件”；
- “使用最新版 Cleaner 重新清洗并更新数据”；
- 删除二次确认。

前端只负责交互和展示，不自行决定 Owner 权限、Pass Bin、Spec 匹配、单位换算或权威统计。

### 4.2 FastAPI 应用模块

| 模块 | 责任 |
|---|---|
| `identity` | 登录、会话、普通用户/管理员权限 |
| `tasks` | 上传任务、源文件回执、状态和缺失能力 |
| `cleaner_registry` | Cleaner/Profile/Release 查询和管理员管理 |
| `jobs` | 入库、下载导出、重清洗任务提交与状态 |
| `enrichment` | 任务级补录及前后值记录 |
| `query` | 服务端筛选、排序、分页和聚合 |
| `charts` | Yield、Bin、Wafer Map、参数统计等图表数据接口 |
| `downloads` | 临时包授权下载和到期处理 |
| `administration` | 用户、Cleaner、全局任务和运行错误管理 |

### 4.3 Worker

Worker 不包含厂家业务规则。它执行固定编排：领取 Job → 解析运行合同 → 准备输入 → 启动 Cleaner 子进程 → 验证输出 Manifest → 解析标准 Excel → 写暂存版本 → 基础校验 → 原子设为 Current → 清理临时目录。

默认只运行 1 个 Worker，避免同机多个重型 Excel 清洗互相争用；压测证明有需要后配置为 2 个。队列领取必须使用原子更新、`lease_owner`、`lease_expires_at`、`heartbeat_at`，宕机后过期任务可重新领取。

## 5. Cleaner 接入合同

### 5.1 Registry，而不是硬编码

沿用 `ingestion.format_profile` 与 `ingestion.cleaner_release`，补充以下可执行合同字段或关联表：

- `test_stage`、`factory_code`、`cleaner_code`、`cleaner_version`；
- `artifact_uri`、`artifact_sha256`、`runtime_uri`；
- `entrypoint` 或受控命令模板；
- `input_contract_version`、`output_contract_version`；
- `status=DRAFT/RELEASED/OBSOLETE`、`released_at`；
- 支持的文件角色、后缀和参数 JSON Schema；
- 超时、最大输出体积和允许的退出码。

每次运行固定记录具体 `cleaner_release_id`。普通首次入库使用用户所选厂家当前已发布版本；默认下载和显式重清洗在任务创建时解析并固定“当时最新版”，避免排队期间版本漂移。

### 5.2 子进程合同

TMS 向 Cleaner 提供只含路径和任务上下文的输入 Manifest；Cleaner 在独立进程和独立临时目录运行，输出 Manifest 至少列出：

- Cleaner 名称、版本、输出合同版本；
- 原始输入文件及 SHA256；
- RawData、Spec、Statistics 三个 Excel 的相对路径、角色、SHA256、行数；
- Cleaner 执行状态、警告和错误摘要；
- 可选的 Lot、Unit、Measurement 汇总。

TMS 不在调用端硬编码 `outlier_method='iqr'`、单位转换或厂家内部 Python import。若 Cleaner 需要参数，参数默认值和校验属于 Cleaner Release 合同，并与版本一起冻结。

### 5.3 新厂家扩展

新增晶圆厂/封测厂时：在原 CP/FT 工具中实现并发布 Cleaner → 用真实样例批准三个 Excel 合同 → 登记 Format Profile/Cleaner Release → 增加轻量 Output Adapter → 跑 Golden Test → 管理员发布。TMS 主任务、权限、数据库和图表框架不增加厂家分支。

## 6. 三条关键数据流

### 6.1 首次上传并自动正式入库

1. API 从会话写入 `owner_user_id`，登记每次上传任务和文件回执；相同 SHA/Lot 只提示，不合并、不拒绝。
2. 原始文件存入或关联 FTP，记录 URI、SHA256、大小和原文件名。
3. 创建 `INITIAL_IMPORT` Job 并立即返回任务编号。
4. Worker 固定 Cleaner Release，在独立目录调用 Cleaner。
5. 校验三个 Excel 都存在、可读且属于本次运行；核对 RawData、Spec、Statistics 基础数量关系。
6. Canonical Importer 写入新的内部 Dataset Version；允许 Product/Lot/Spec 等业务字段为空。
7. 校验通过后在一个短事务中自动把新 Version 设为 Current，任务变为 `READY`；无需用户审核。
8. 根据缺失字段生成能力提示；Cleaner Excel 在成功入库和超过短期诊断 TTL 后删除。

失败时不产生 Current Version，任务显示可读错误，FTP 原始输入仍保留。

### 6.2 使用最新版 Cleaner 生成下载文件

1. 用户对有权任务创建 `EXPORT_LATEST` Job；系统展示数据库当前 Cleaner 版本与将使用的最新版。
2. Worker 从 FTP 取回原始输入，用固定的最新版 Cleaner 在临时目录生成三个 Excel。
3. 三个文件全部校验成功后打包，登记临时下载 URI、SHA256、到期时间。
4. 本流程**不调用 Canonical Importer、不创建 Current Version、不修改补录**。
5. 下载完成后可延迟清理，未下载则按 TTL 清理；失败只影响本次导出。

### 6.3 使用最新版 Cleaner 重新清洗并更新数据

1. 用户二次确认后创建 `REPROCESS_UPDATE` Job，保留任务级补录。
2. Worker 生成新输出，并写入新的非 Current Dataset Version。
3. 全部入库与基础校验成功后，在同一事务中把旧 Current 设为 Superseded、新 Version 设为 Current。
4. 失败时回滚新版本或标记失败，旧 Current 指针和查询结果不变。
5. 成功后所有查询和图表自动读取新 Current；补录仍通过任务上下文叠加，不改写 Cleaner 原始值。

## 7. 唯一 Canonical 数据模型

### 7.1 对象关系

```text
iam.app_user
  1 ── N ingestion.import_batch（上传任务/Owner 边界）
          1 ── N ingestion.source_file_receipt / import_batch_file
          1 ── N ingestion.processing_job
          1 ── N ingestion.processing_run（每次 Cleaner 运行）
          1 ── 1 dataset.dataset
                  1 ── N dataset.dataset_version（内部更新版本）
                            N ── N ingestion.processing_run
                                      1 ── N test.test_run（按 Lot/测试上下文）
                                                1 ── N test.unit_result
                                                          1 ── N test.measurement

ingestion.field_enrichment ──> import_batch（任务级人工上下文）
test.test_run / 第一批次 Spec ──> mdm.spec_set / mdm.spec_item
```

### 7.2 数据语义

- `import_batch` 是权限、删除、补录和用户操作的边界，不是 Lot；
- `processing_run` 固定输入、Cleaner Release、输出 Manifest 和校验结果；
- `test_run` 表示某个 Lot/测试程序/条件下的运行；一个上传任务可有多个；
- `unit_result` 表示 CP Die 或 FT Unit，保存 Raw Lot、Wafer、序号、X/Y、Raw Bin、Pass/Fail；
- `measurement` 保存 `raw_value`、numeric/text 值和 `measurement_status`，NULL 不等于 NOT_TESTED；
- Cleaner 已提供的 Yield/统计可以存带来源的快照，但图表在筛选后必须由 Current 结构化明细重新聚合；
- 原始字段与人工补录分别存储，查询层提供 `effective_*` 字段，不回写覆盖原始字段。

### 7.3 多 Lot 与 Spec

首版沿用现有程序规则：

- 每条 Unit 继续保留 Cleaner 给出的 Lot_ID；
- 业务用户只把相同 Spec 的批次纳入同一次比较；
- 多批次分析使用用户所选批次顺序中的第一批次 Spec；
- 首版不新增多 Lot Spec fingerprint、自动比对或 Lot Binding；
- 不同 Spec 的自动识别和逐 Lot 使用不属于当前功能启动范围，待核心功能完成后另行设计。

### 7.4 缺失字段和补录

`field_enrichment` 记录字段、旧值、新值、操作者、时间和作用范围。读取时采用：

```text
effective_value = Cleaner 原始值非空 ? Cleaner 原始值 : 当前任务级补录值
```

若 Cleaner 已有不同 Lot_ID，任务级补录不得覆盖。无 Lot_ID 时任务补录可作用于全部适用 Unit；如果实际混合多个未知 Lot，系统明确告知不能自动拆分。

### 7.5 运行事实与企业主数据分开

现有 Canonical 的部分 Test Program/Test Item 关系要求 Product 等主数据先存在，这与“缺 Product 仍可正式入库”冲突。Route A 不得创建 `UNKNOWN_PRODUCT` 等伪主数据来绕过约束。

目标设计把 Cleaner 输出的参数定义先作为 `processing_run/dataset_version` 范围内的运行事实保存；只有 Product、Program、Parameter 身份明确且命中已批准映射时，才关联 `mdm.*` 企业主数据。实现可以对现有表做兼容性扩展，或增加运行级定义表，但最终查询仍通过唯一 `test.*` 明细链，不能借此重建另一套 `analysis.*` 事实表。

## 8. 权限与安全

首版权限只有两个业务数据范围：

- 普通用户：`import_batch.owner_user_id = current_user_id`；
- 管理员：基于明确的 Admin 权限查看和操作全部数据。

所有列表、明细、图表、下载、补录、重清洗和删除 API 都从任务 Owner 开始授权；不能只在前端隐藏按钮。任何由浏览器提交的 owner、文件路径、Cleaner 路径或管理员标志都不可信。

Cleaner 子进程使用低权限服务账户，限制工作目录、超时、输出体积和可访问路径；命令来自已发布 Registry，不接受用户拼接。日志不得记录测量明细、密码、令牌或 FTP 凭据。

## 9. 查询与图表架构

SQL Server 执行 Owner 过滤、Current Version 过滤、Lot/Wafer/Bin/Parameter 条件、分页和聚合；FastAPI 返回表格页和图表所需聚合。ECharts 只渲染返回结果。

建议第一批查询索引围绕：

- `import_batch(owner_user_id, test_stage, started_at_utc)`；
- Current Dataset Version；
- `test_run(processing_run_id, lot_id)`；
- `unit_result(test_run_id, wafer_id, soft_bin/raw_bin, x, y)`；
- `measurement(unit_id, test_item_id)` 和按参数的分析访问路径。

具体索引必须用真实华虹/日月新规模执行计划和压测确定，不凭表结构预设过多索引。

## 10. 状态、事务与幂等

### 10.1 用户任务状态

```text
UPLOADED → QUEUED → CLEANING → IMPORTING → READY
                         └──────────────→ FAILED
READY → REPROCESSING → READY（成功切换或失败保持旧数据）
READY/FAILED → DELETING → DELETED（物理清理后任务不再可见）
```

### 10.2 Job 类型

- `INITIAL_IMPORT`
- `EXPORT_LATEST`
- `REPROCESS_UPDATE`
- `DELETE_TASK`
- 后续可增加 `REBUILD_AGGREGATE`，不能复用含糊的 `OTHER`。

每个用户动作带服务端生成的 idempotency key；Worker 对同一 Job 只允许一个有效租约。结构化导入以 `processing_run_id` 隔离，Current 切换使用 SQL Server 事务和唯一索引，避免半套新数据可见。

## 11. 删除与生命周期

删除以前端明确确认开始，由后台事务/Job 按外键顺序清理该 `import_batch` 的 Dataset、Run、Unit、Measurement、统计、补录、运行记录和临时导出。删除只以任务 ID + Owner/Admin 授权定位，绝不按 Lot_ID 或 SHA256 批量删除。

FTP URI 只是外部引用，删除流程禁止调用 FTP 删除。若 `source_file` 被多个上传回执引用，只删除当前任务的关联和回执；共享文件台账是否保留由引用计数决定。

建议初始 TTL（配置项，需部署前确认）：失败运行临时目录 7 天、成功入库临时 Excel 24 小时、下载包 24 小时。TTL 是当前技术默认，不是已冻结业务期限。

## 12. 可观测性与恢复

- 每个 API 请求、Job、Cleaner Run、Dataset Version 使用相关 ID 串联；
- 记录状态时间、Cleaner 版本、退出码、耗时、输入/输出行数和错误摘要；
- 管理员看到失败原因和重试入口，普通用户看到可行动的业务提示；
- SQL Server 做全量/差异/日志备份并进行恢复演练；FTP 原始文件由现有机制保障；
- Worker 重启测试需证明租约过期后可恢复，且不会产生两个 Current Version。

## 13. 从当前实现迁移

| 当前情况 | Route A 目标 | 处理方式 |
|---|---|---|
| 上传 API 同步执行 Cleaner | API 快速返回，Worker 异步执行 | 保留接口语义，改为提交 Job |
| CP/FT 路径、厂家和内部 import 硬编码 | Registry + 稳定子进程合同 | 重构 `ExistingCleanerRunner` 为通用 Runner + Output Adapter |
| 调用端硬编码 IQR/单位转换 | 参数由 Cleaner Release 决定 | 从 TMS 删除厂家清洗参数 |
| 只写 `processing_result_summary` | 三个 Excel 进入唯一 Canonical | 摘要改为可重建的列表投影 |
| `test.*` 与 `analysis.*` 重复 | `test.*` 唯一事实源 | 新 migration 停用/删除 `analysis.*`，先检查是否已有数据 |
| 人工 Dataset 审核/发布 | 校验通过自动 Current | 保留内部原子版本，移除用户审核步骤 |
| 重清洗先删除旧输出目录 | 新运行隔离，成功后切换 | 禁止成功前清理现有可用数据 |
| Global data scope 逻辑 | Owner 或 Admin 两级 | 收紧普通用户查询；管理员显式权限 |
| 本地 `data/raw` 是原始输入 | FTP/Storage Adapter | 保留开发实现，生产切换 FTP Adapter |

迁移实施顺序：先盘点 `analysis.*` 是否有数据和依赖 → 增加 Route A 所需字段/绑定/队列租约 → 实现新 Writer → 双读对账但不双写 → 查询切到 `test.*` → 移除 Route B 写入口/表。任何共享环境已执行 migration 均不回写。

## 14. 架构验收门槛

- BR-01～BR-05：三个 Excel 均可追溯写入 Run/Unit/Measurement/Spec，缺失字段不伪造；
- BR-02/BR-03：多批次比较沿用第一批次 Spec，并在界面说明只允许选择相同 Spec 批次；
- BR-06/BR-10：列表、明细、图表、下载、补录、重清洗和删除均通过越权测试；
- BR-07：最新版导出不产生新的 Current Version，数据库行数和版本不变；
- BR-08：故意让新 Cleaner 失败，旧数据仍完整可查；成功时只存在一个 Current；
- BR-09：只清理目标任务的 TMS 数据，FTP 和其他用户同 Lot 数据不变；
- 真实 Golden 样例对账 RawData 行数、Unit、Measurement、Lot/Wafer、Bin、坐标、Spec、统计；
- SQL Server 2014 Compatibility 120 下 migration、查询、索引和恢复测试通过。

## 15. 待后续业务确认但不阻塞骨架

- 第一批图表的准确优先级；
- CP/FT 各自最终补录字段清单；
- 新厂家 Cleaner 接入顺序；
- 临时文件 TTL、数据库 RPO/RTO 和容量目标；
- FTP 是由 TMS 主动上传，还是只登记既有归档路径。Storage Adapter 会隔离该差异。
