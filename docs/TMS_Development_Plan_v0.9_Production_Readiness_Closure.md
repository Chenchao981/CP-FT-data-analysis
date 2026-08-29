# TMS v0.9 生产就绪收口开发计划

- 计划日期：2026-08-29
- 计划状态：仓库与开发库 G0-G2 已完成；实现为 `sql2014_0018`；G3/G4 未执行
- 目标版本：TMS v1.0 Core
- 适用仓库：`F:\CP-FT数据分析`
- 目标环境：Windows Server 2019 + SQL Server 2014 SP3（正式环境仍须现场复验）
- 执行优先级：本计划是当前生产就绪收口的唯一执行计划；v0.7/v0.8 保留为历史阶段证据
- 上位业务口径：`business/TMS_Capability_Classification_v1.0_2026-08-27.md`
- 正式入库架构：`architecture/TMS_System_Architecture_v0.7_Route_A.md`
- 快速分析架构：`architecture/TMS_System_Architecture_v0.8_Dual_Channel.md`

## 1. 计划目的与“完成”定义

本计划把已有可运行原型收口为可灰度、可回归、可审计的 TMS v1.0 Core。开发顺序不是继续堆叠图表，而是先关闭数据错误、安全越界和不可恢复故障窗口，再改善一线工程师工作台和管理视图，最后用真实样本、普通权限账号和可回滚发布流程验收。

“项目完成”分为两个层级，禁止混用：

1. **仓库交付完成**：本计划范围内的代码、Migration、自动化测试、构建、本机认证灰度、开发库真实样本回归、操作说明和报告全部通过，且不存在未说明的 P0 缺陷。
2. **生产上线完成**：在仓库交付完成基础上，由新洁能提供目标服务器、正式服务账号、网络与证书、备份恢复条件、业务 UAT 人员和发布窗口，完成现场灰度、回滚演练、数据对账与签字。

没有目标生产环境权限时，只能得出“仓库交付完成”或“开发库灰度通过”，不能写成“生产已上线”。

## 2. 当前事实、假设、冲突与开放门

### 2.1 已确认事实

- 当前唯一正式明细链是 `test.test_run -> test.unit_result -> test.measurement`；快速分析和临时 Workspace 不得写入该链。
- 仓库唯一 Alembic head 和开发库 `TMS_G0_DEV` 均为 `sql2014_0018`；0015 原子 finalize、0016 Worker 运维、0017 管理/crosswalk、0018 A5 生命周期已经在开发库顺序升级验证。
- 现有本机入口能启动 API、Route A Worker 和 React 前端；真实日月新 FT 已跑通上传、SQL Queue、Cleaner、Canonical、Dataset Current 和分析图表。
- 2026-08-28 的后端 206 项、前端 34 项是计划启动前历史基线；最终为后端 `393 passed, 1 skipped`、前端 25 files / 91 tests、生产 build PASS，发布包 SHA 见 `docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md`。
- 日月新/日月光缺 Lot 已具备“暂停 -> 文件级补录 -> 同 Release 恢复 Job -> 正式发布”的审计闭环。
- CP 和 FT 保持两个独立程序合同；菜单固定为工程 CP、工程 FT、量产 CP、量产 FT，不增加自动类型猜测。
- 新洁能的业务背景要求系统能够支持多晶圆厂/封测厂协同、功率器件多产品平台、车规质量追溯与生产周期数字化；SAP、MES、QMS 的自动集成只有在接口和主数据责任明确后才进入生产范围。

### 2.1.1 2026-08-29 执行快照

| 计划项 | 当前事实 | 完成边界 |
|---|---|---|
| M1 数据安全 | Source Catalog、Lot-Spec、NULL Yield、全部来源血缘、staged/atomic finalize 已实现；7 个事务边界故障注入通过 | 完成；最终全量 PASS |
| M2 工程师闭环 | Dataset Current、服务端分页/筛选、Job 详情、深链和四入口已实现 | 完成；最终浏览器回归 PASS |
| M3 质量/领导视图 | 质量 KPI、未知占比、下钻、产品 crosswalk 和 SAP/MES/QMS 合同已实现 | 专项完成；企业主数据批准属于外部门 |
| M4 运维/生命周期 | Export/Archive/Reprocess、Worker registry、双 Cleanup、备份恢复与发布工具已实现 | 开发库/DryRun 完成；目标机执行属于 G3 |
| M5-M7 | 真实 CP/FT、Quick PAT、空库 Migration、A5 回滚、全量、浏览器和 release hash 已取得 | 完成；G0-G2 PASS，G3/G4 未执行 |

开发库正式事实在升级和回滚验证前后保持 139 Test Run、291,127 Unit Result、5,578,114 Measurement、10 个 Published Current Dataset Version。G3 测试服务器和 G4 生产分批均未执行。

### 2.2 本计划采用的假设

- 原始 CP/FT 数据只读，测试使用字节级副本并记录 SHA-256，不把样本、输出、日志或秘密提交到 Git。
- Cleaner、单位换算、Bin、Spec 和统计公式继续以已发布 CP/FT 工具为权威，TMS 只通过 Adapter/Worker 调用和校验。
- 正式环境继续采用 SQL Server 2014 兼容 SQL；开发库可以实施和回滚新 Migration。
- 正式上线前 SQL Server 升级至 SP3，并重新执行 Migration、并发、备份恢复和性能验证。

### 2.3 必须消除的口径冲突

| 冲突 | 最终决策 | 验收方式 |
|---|---|---|
| 旧文档允许整批使用首个 Spec；新能力基线要求按 Lot 绑定 | 以 Lot 级 Spec 为准；无法证明一致时失败关闭 | 多 Lot 同 Spec、异 Spec、同 Lot 冲突三类测试 |
| 本机正式上传允许任意绝对 `source_path` | 改为管理员配置的 Source Catalog + 相对路径；解析后仍必须位于允许根 | 路径穿越、联接点/符号链接、越根和扩展名负向测试 |
| Dataset 发布、结果摘要、Batch、Job 终态跨事务 | 使用 staged + 幂等 finalize；异常后可重放并对账 | 每个提交边界故障注入与重复执行测试 |
| FT 源数据无 PASS/FAIL 时通用摘要可能显示 0% | PASS、FAIL、良率保持空值，并明确 `UNKNOWN` | API、页面和真实日月新样本对账 |
| 免认证本机模式掩盖前端漏带 Token | 所有受保护请求统一走认证请求层，401 统一清理会话 | 普通账号和过期 Token 合同测试 |

从本计划生效起，`TMS_Business_Requirements_v0.2.md` 和 `TMS_Development_Plan_v0.7_Route_A.md` 中的 `FIRST_BATCH` / “第一批次 Spec”条款失效；`TMS_Development_Plan_v0.8_Dual_Channel.md` 中“SQL Server 不可达、Q0 在线链未完成”的状态仅为当时记录，不再代表当前进度。其他未冲突业务规则继续有效。

### 2.4 外部生产门

以下项目需要新洁能基础设施或业务授权，不能由仓库内代码单方面完成：

- 正式 Windows Server、服务账号和受保护的计划任务凭据；
- 反向代理、HTTPS 证书、防火墙、DNS 和局域网访问策略；
- 正式 SQL Server SP3、备份、恢复点和恢复演练窗口；
- SAP-B1 产品/物料、供应商、工艺版本的主数据责任人与接口；
- CP/FT 真实 Golden 批次、业务 Owner、质量人员和普通角色 UAT 账号；
- 生产灰度范围、停机/回滚阈值及最终签字人。

## 3. 本版范围与非范围

### 3.1 TMS v1.0 Core 必须交付

1. 四个正式入口、定制工具入口和快速分析入口边界清晰。
2. 已批准厂家/格式的严格识别、Cleaner Release、Canonical 入库、Dataset Current 和可追溯结果。
3. Lot 级 Spec、所有来源文件血缘、未知良率空值和幂等最终发布。
4. RBAC、Owner 隔离、受控 Source Catalog、安全配置和完整审计。
5. 一线工程师可按产品、Lot、厂家、阶段、状态和时间找到数据，不依赖手填 Dataset ID。
6. 任务页能说明排队、运行、待补录、失败、成功，显示 Cleaner/Job/Batch/Dataset 和可操作的错误原因。
7. 领导/质量视图只展示有事实来源的产量、良率、异常、数据新鲜度和厂家趋势；未知数据不补零。
8. 自动化测试、开发库真实样本 E2E、本机认证灰度、回滚演练、全量回归和报告。

### 3.2 本版明确不伪装完成

- 未取得接口合同前，不宣称 SAP-B1、MES、QMS、FDC、设备数据已自动集成。
- 未独立完成 Factory + Format Profile + Release + Adapter + SQL 对账的厂家，不开放正式提交。
- 不把日月新/日月光的验收外推为电基、集佳、杰群正式 Route A 已通过。
- 不把快速 PAT 或临时 Workspace 结果宣传为正式 Measurement。
- 不在 v1.0 内重写已有厂家 Parser、Bin、单位、Spec 或统计公式。

## 4. 工作包、顺序和关闭条件

### M0：计划冻结与基线留证

交付：本计划、风险台账、当前测试基线、Git 状态和外部生产门清单。

关闭条件：计划中的每个 P0/P1 都有实现路径、测试证据和失败回退；旧文档冲突有明确优先级。

### M1：P0 数据正确性和安全收口

交付：

- 前端受保护 API 统一携带 Bearer Token，401 统一退出；
- 正式上传复用 Source Catalog/相对路径合同，取消普通用户任意绝对路径；
- Dataset 汇总的 PASS/FAIL/良率支持空值，不把未知显示为 0%；
- CP Spec 改为 Lot 级绑定；无法确定时停止发布；
- Route A staged + 幂等 finalize，结果摘要、Batch、Job 和 Dataset 可恢复一致；
- processing run 与所有来源文件建立数据库级映射；
- 生产模式 JWT/认证配置失败关闭，Cleaner 子进程只继承允许的环境变量；
- 对应 SQL Server 2014 Migration、回滚说明和故障注入测试。

关闭条件：全部 P0 自动化测试通过；任何未知 Spec、越根路径、缺 Token 或 finalize 重试都不能发布错误 Current。

### M2：一线工程师使用闭环

交付：

- Dataset Current 目录页，支持产品、Lot、厂家、工程/量产、CP/FT、状态和时间检索；
- URL 可深链，刷新后保持当前入口、Dataset 和筛选条件；
- 上传/结果服务端分页与筛选，避免全表加载；
- Job 详情显示状态历史、父子 Job、Cleaner Release、错误分类、开始/结束和可执行动作；
- 下载失败、补录冲突、权限不足和 Worker 不可用给出明确页面反馈；
- API、数据库、Worker、Schema 和当前环境在页面可见，移除硬编码“开发环境”；
- 保留快速计算、临时 Workspace、正式入库三条边界。

关闭条件：一线用户无需查询数据库或手填内部 ID，即可从上传找到结果、异常原因和分析页面；关键流程具备前端合同测试与浏览器验收。

### M3：质量与领导视图

交付：

- 按时间、产品、Lot、厂家、阶段汇总的产量/良率/Fail Bin/异常任务/数据新鲜度；
- 所有 KPI 显示口径、时间范围、样本数和未知占比；
- 能从汇总下钻到 Dataset、Lot、Source 和 Job；
- 预留 SAP 物料、供应商和工艺版本 crosswalk，不自动把源文本升级为企业主数据；
- 输出 SAP/MES/QMS 后续接口合同清单，而不是在缺少接口时制造假集成。

关闭条件：领导看到的每个数字都能下钻到正式事实；无 PASS/FAIL 的数据不进入良率分母；Owner 与跨部门访问符合批准角色。

### M4：运维、留存与发布能力

交付：

- 最新 Cleaner 临时导出与显式重清洗语义分离；
- 完成 A5：最新 Cleaner 非变异导出、失败保护的显式重清洗、Owner/Admin 受审计删除；FTP/NAS 原始源禁止由 TMS 删除；
- Artifact/Workspace TTL、安全清理、容量、审计和 DryRun；
- Worker 心跳、单实例/drain、停机恢复和告警接口；
- 生产配置样板、ACL 检查、计划任务安装/卸载、日志轮转和健康探针；
- 数据库备份/恢复、Migration 前后检查和应用回滚手册。

关闭条件：发布包不包含样本、秘密和本地配置；旧版本应用与数据库回滚边界清楚；清理不得越出受管根。

### M5：自动化测试与开发库真实样本回归

测试层级：

1. 后端单元/合同测试；
2. 前端组件/API 合同测试和生产构建；
3. SQL Server 2014 空库升级、现有库升级、约束、幂等和回滚检查；
4. Worker/Cleaner 隔离子进程、Release SHA、路径安全和故障恢复；
5. 已批准真实 CP/FT Golden 样本的行数、Lot、Wafer、参数、Spec、Bin、良率和 Current 对账；
6. 浏览器端普通账号上传、补录、检索、分析、权限拒绝和下载；
7. Quick Analysis 验证 `test.*` 前后行数不变；
8. 启停、重启、Worker 中断、重复请求和大数据性能回归。

关闭条件：P0/P1 零开放；全量自动化通过；所有真实样本保留 SHA 和对账结果；失败用例证明系统按设计失败关闭。

### M6：灰度与回滚演练

| 灰度级别 | 环境与用户 | 数据范围 | 通过门 | 回退条件 |
|---|---|---|---|---|
| G0 | 本机开发管理员 | 合成/小样本 | 全量自动化、构建、Migration 静态检查 | 任一 P0 失败 |
| G1 | 本机认证模式普通角色 | 真实样本副本 | Owner/RBAC、Token、四入口和异常闭环 | 越权、错误 Current、原文件变化 |
| G2 | `TMS_G0_DEV` 开发库 | 已批准 CP/FT Golden | SQL/Worker/E2E 对账、重启恢复、回滚演练 | 对账差异、不可恢复 Job、性能越线 |
| G3 | 测试服务器小组试用 | 1 个小组、有限产品/Lot | HTTPS、服务账号、备份恢复、业务 UAT | 重大安全/数据/稳定性问题 |
| G4 | 生产分批 | 先单厂家/单阶段，再扩围 | 连续运行、业务签字、监控告警 | 触发批准的回滚阈值 |

本次仓库收口目标是完成 G0-G2；最终 Gate 以日期化报告中的最后一次全量结果为准。G3-G4 必须使用目标环境和正式授权，不以本机结果替代，也不得因开发库通过而写成生产上线。

### M7：最终回归、报告与交付

必须输出：

- `docs/development/TMS_v1.0_M1_Data_Safety_Completion_Report_2026-08-29.md`
- `docs/development/TMS_v1.0_Gray_Release_Report_2026-08-29.md`
- `docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md`
- `docs/development/TMS_v1.0_Core_Completion_Report_2026-08-29.md`
- 更新 README、开发/生产运行说明、Migration 与回滚手册。

每份完成报告必须分开写“做了什么、确定的、不确定的、验证证据、下一步”，并记录测试时间、命令入口、版本、数据库身份、样本 SHA、耗时与未关闭外部门。

## 5. 验收矩阵

| 质量目标 | 必须证据 | 不通过示例 |
|---|---|---|
| 数据正确 | Golden 对账、Lot-Spec 指纹、Unit × Parameter 数量关系、Current 唯一性 | 未知良率显示 0%、跨 Lot 共用错误 Spec |
| 安全 | 受控根、路径攻击测试、RBAC/Owner、秘密扫描、生产配置拒绝弱默认值 | 任意绝对路径、普通用户跨 Owner 访问 |
| 可恢复 | finalize 故障注入、幂等重放、Worker 重启、父子 Job 和审计 | Dataset 已发布但 Job 永久失败且无法对账 |
| 可用 | 普通账号浏览器任务、检索、补录、错误说明、深链 | 依赖手填 Dataset ID 或查 SQL 才能继续 |
| 性能 | 实际文件数/行数、耗时、峰值内存、临时磁盘和结果大小 | 只用小样本推断大数据性能 |
| 可运维 | ready/heartbeat、日志、备份恢复、安装卸载、回滚 | 只证明端口打开，不证明数据库/Worker 身份 |
| 可审计 | 用户、时间、来源、Release/SHA、Job、Dataset Version、导出/删除记录 | 原地覆盖历史事实或丢失非首文件血缘 |

## 6. 回归数据集与口径

回归样本优先使用已有只读 Golden 和受控副本：

- 日月新 FT：已知 Lot、缺 Lot 后补录、无 PASS/FAIL；
- 日月光 FT：已知 Lot、缺 Lot后补录、错误厂家/未知头部；
- 华虹 CP：严格 TXT/归档、聚合行排除、Lot/Wafer/Bin/Yield；
- Jetech/立昂微 CP：只在当前机器存在已批准样本且输出合同可对账时计入真实通过；
- Quick PAT：验证结果 Manifest、公式、性能、TTL，并确认 Canonical 行数不变。

没有真实样本或业务批准的厂家，只能完成代码合同和负向测试，不写成真实生产能力已验收。

## 7. 缺陷等级与发布规则

- **P0**：错误数据发布、跨 Lot/Owner 污染、原文件修改、越根读写、秘密泄漏、无法恢复的一致性故障。任一 P0 阻断所有灰度扩围。
- **P1**：关键流程无法完成、结果不可追溯、普通角色无法使用、恢复/回滚不可执行。G2 前必须清零。
- **P2**：效率、易用性、非关键图表和性能优化。可带明确责任人和日期进入后续版本，但不得影响数据口径。
- **P3**：视觉细节和建议项，不影响当前发布门。

每次修复必须包含复现测试；只改代码没有回归证据不得关闭缺陷。

## 8. 角色与业务签字

| 事项 | 开发/运维证据 | 业务签字建议 |
|---|---|---|
| 厂家格式、Lot、Spec、Bin、单位 | Adapter/Golden 对账 | CP/FT 工程、质量 |
| 产品/物料/供应商主数据 | crosswalk 差异清单 | SAP-B1 主数据 Owner、财务/供应链 |
| 权限与 Owner 范围 | 角色矩阵、越权测试 | CIO/信息安全、部门负责人 |
| 灰度产品和批次 | G3/G4 清单、回退阈值 | 生产、质量、IT 运维 |
| 最终上线 | 测试、备份恢复、监控、UAT 报告 | CIO、业务 Owner、运维 |

## 9. 执行与变更控制

1. 严格按 M0 -> M1 -> M2/M3 -> M4 -> M5 -> M6 -> M7 推进；P0 未关闭时不扩展灰度。
2. 每个里程碑结束先运行对应专项测试，再运行全量回归，随后形成日期化报告。
3. 数据合同变化必须同步后端 DTO、SQL、前端类型、测试和业务文档。
4. 数据库变更只通过新 Migration 前进，不修改已经执行的历史 Migration。
5. 用户工作区已有改动和 `.remember/` 不纳入提交；原始数据、输出、日志、缓存、账号和秘密永不提交。
6. 已验证的源码和文档按里程碑提交；推送前复查暂存清单、测试证据和敏感文件。

## 10. 最终 Definition of Done

只有同时满足以下条件，才能把 TMS v1.0 Core 标记为仓库交付完成：

- M1-M5 全部关闭，P0/P1 为零；
- G0-G2 通过，且报告明确未执行的 G3-G4 外部门；
- 后端、前端、Migration、Worker、真实样本、浏览器、构建和回滚证据完整；
- 文档与实际页面/API/Schema 一致，无“首批 Spec”等已废弃口径继续作为当前规则；
- 所有未知值保持未知，所有正式数字可以追溯到 Source、Release、Job、Run 和 Dataset Version；
- 提交只包含安全源码、Migration、测试与文档，且已推送到批准的远端分支。

生产上线完成还必须额外满足：G3-G4、HTTPS、正式账号、备份恢复、持续运行、业务 UAT 和正式签字全部通过。

截至 2026-08-29，最后一次后端/前端/浏览器/真实 SQL/发布包验证和安全源码提交已经完成，仓库与开发库 G0-G2 判定为 PASS。G3/G4 当前明确未执行，因此生产上线状态仍为“未上线”。
