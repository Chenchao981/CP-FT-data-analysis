# TMS 开发规划 v0.7（Route A）

状态：**候选执行计划，待需求与架构评审确认**

形成日期：2026-08-24

业务基线：`docs/business/TMS_Business_Requirements_v0.2.md`

架构基线：`docs/architecture/TMS_System_Architecture_v0.7_Route_A.md`

## 1. 开发目标

围绕现有 Python Cleaner 建成可投入内部使用的 CP/FT 平台闭环：上传原始文件 → 后台调用 Cleaner → 读取 RawData/Spec/Statistics 三个 Excel → 自动正式入库 → 缺失字段补录 → 历史查询与绘图 → 最新版 Cleaner 临时导出 → 显式重清洗原子更新 → Owner/Admin 删除。

开发以可运行的 Vertical Slice 验收，不以“表建完”“接口写完”或代码百分比验收。首条 Slice 选择华虹 CP，第二条选择日月新 FT；每条都必须用原 Cleaner 和真实 Golden 样例对账。

## 2. 当前基础判断

### 2.1 可以复用的好基础

- FastAPI、React/TypeScript、SQL Server 2014 migration 和认证骨架已存在；
- `format_profile`、`cleaner_release`、`processing_job`、`processing_run` 已具备可演进基础；
- `test.test_run / unit_result / measurement` 已实现明确测量状态和来源追溯；
- Dataset Current 原子切换已有实现，可改造成用户无感的失败保护；
- 华虹严格解析、压缩包安全、Canonical Writer、DQ 和真实数据库验证已有资产；
- Owner 字段、登录、角色权限、任务列表和 CP/FT 上传入口已有部分实现。

### 2.2 必须纠正的问题

- 当前量产上传在 Web 请求内同步清洗，长任务会占住请求且无法可靠恢复；
- 上传成功后只写结果摘要，三个 Cleaner 输出没有形成长期结构化明细；
- `test.*` 与 `analysis.*` 两套明细模型争夺事实源；
- 调用器硬编码 Cleaner 路径、厂家分支、Python 内部 import 和 IQR 参数；
- 当前输出合同仍有 CSV/Excel 口径差异，必须以现行 Cleaner 的三个 Excel 和 Golden 样例重新冻结；
- 当前重清洗在成功前删除旧输出目录，不满足失败保护；
- 旧 v0.6 的人工审核/发布、复杂 Data Scope 和长期 Export Artifact 与 v0.2 业务口径冲突；
- 现有 `mdm.test_program.product_id` 等主数据约束可能把缺 Product 的合法任务挡在 Canonical 之外，需要增加任务级/运行级定义边界，不能用伪造 Product 过约束；

## 3. 开发原则

1. **Cleaner First**：厂家清洗规则只在原 Cleaner 更新，TMS 只消费版本化输出合同。
2. **One Canonical**：只有 `test.*` Canonical 是明细事实源；摘要和图表聚合均可重建。
3. **Atomic Current**：首次成功自动正式；重清洗先写新版本，成功才原子切换。
4. **Owner Boundary**：权限、查询、导出、补录和删除均从上传任务 Owner 判定。
5. **Fail Closed**：输出文件缺失、格式未知、数量明显矛盾或写入不完整时，不产生正式 Current 数据。
6. **No Guessing**：缺 Product/Lot/Spec 保持 NULL；允许能力降级和后补录。
7. **Real Sample Acceptance**：每个 Cleaner Release 以真实样例、SHA256 和 Golden Manifest 对账。
8. **Forward Migration Only**：已经执行的 Alembic revision 不改写，只增加后续 revision。

## 4. 分阶段路线

### A0：基线收敛与技术门禁

目标：在写新功能前消除文档和事实源歧义。

交付：

- 评审并批准业务需求 v0.2、架构 v0.7 和本计划；
- ADR：Route A、唯一 Canonical、内部自动 Current、SQL 数据库队列；
- 只读盘点开发/共享数据库 `analysis.*` 表数据量、外键、View、API 和报表依赖；
- 用当前 CP/FT Cleaner 各跑批准样例，冻结三个 Excel 的文件名、Sheet、列、类型、空值、单位、Spec 和统计合同；
- 为华虹 CP、日月新 FT 建立 Golden Manifest；
- 明确 FTP 取得原始输入的方式和开发环境 Storage Adapter。

退出条件：

- Route B 明细不再接受新开发；
- 三个 Excel 合同不存在未解释的 CSV/XLSX 冲突；
- `analysis.*` 处置方案有真实数据库证据；
- BR-01～BR-10 可逐条映射到阶段和测试。

### A1：Schema 收敛、队列和运行合同

目标：建立可安全运行 Cleaner 和原子更新的底座。

交付：

- 新 Alembic revision：Job 类型、租约/心跳/重试、Cleaner 可执行合同、运行 Manifest、临时 Artifact/TTL；
- Lot 级 Spec Binding 与任务级 Effective Context 所需字段；
- 允许缺 Product/Lot 的运行级 Test Item/Program 表达；只有明确匹配时才关联企业 MDM，不为入库强造主数据；
- `processing_result_summary` 降级为可重建投影；
- 按 A0 盘点结果停用 `analysis.*`：无数据则后续 drop；有数据则先迁移/校验再 drop；
- 通用 Cleaner Registry Service、Subprocess Runner 和 Storage Adapter；
- Worker 主循环、原子领取、心跳、超时、失败恢复和结构化日志；
- Owner/Admin 后端统一授权策略。

测试：

- 空库升级、已有 `sql2014_0009` 数据库前向升级、downgrade 安全边界；
- 两个 Worker 竞争同一 Job 只有一个成功领取；
- Worker 宕机后租约恢复；
- 未发布/Checksum 不符 Cleaner 被拒绝；
- 普通用户直接构造其他任务 ID 返回 404/403。

退出条件：SQL Server 2014 Compatibility 120 集成测试通过；任务提交后 API 可立即返回；Worker 可可靠完成一个合成 Cleaner Job。

### A2：华虹 CP Route A 端到端

目标：完成第一条真实、可查询、可绘图的生产 Slice。

交付：

- 华虹 Cleaner Release 与三个 Excel Output Adapter；
- RawData → test_run/unit_result/measurement 导入；
- Spec/参数 → Test Item/Spec/测试条件导入；Statistics → 带来源统计快照；
- 单 Lot、多 Lot 入库；多批次分析沿用第一批次 Spec；
- 缺 Product/Lot 弹窗和任务级补录；
- 任务列表、运行详情、缺失能力说明；
- CP 查询筛选、明细、Yield、Bin、Wafer Map 和第一批参数图。

测试与对账：

- BR-01～BR-05 全部自动化；
- Golden 样例逐 Lot/Wafer 对账 Unit、Measurement、Bin、X/Y、Yield 和 Spec；
- 每条明细的 Lot_ID 不得被第一批次覆盖；多批次分析按已冻结业务规则使用第一批次 Spec；
- 缺 Lot 数据补录前后只改变 Effective Context，不改变 Raw 值；
- 图表筛选后的计数与 SQL 明细一致。

退出条件：华虹真实 Golden 样例 100% 满足已批准 Manifest；业务用户能完成上传到二次绘图闭环。

### A3：日月新 FT Route A 端到端

目标：证明框架可扩展到 FT，而不是 CP 专用系统。

交付：

- 日月新 FT Cleaner Release 与三个 Excel Output Adapter；
- Product、Lot、Unit、PASS/FAIL、Bin、参数、条件、Spec 和统计映射；
- FT 缺 Product/Lot 的独立补录和能力提示；
- FT 历史查询、明细、Yield/Bin、参数分布与 Scatter；
- CP/FT 共用任务框架，但不共用厂家 Parser 或错误的字段必填规则。

测试与对账：

- Golden 样例对账 Unit、Measurement、PASS/FAIL、Bin、参数单位/条件/Spec；
- CP 缺 Product、FT 缺 Lot 等差异场景互不阻塞；
- 相似但未批准格式失败关闭；
- BR-06 Owner 隔离覆盖 CP/FT。

退出条件：同一部署内 CP/FT 两条 Slice 均可独立工作，新增 FT Adapter 未修改 CP 清洗逻辑。

### A4：历史查询与通用图表

目标：让数据库结构化数据成为日常分析主体。

交付：

- 上传时间、任务、阶段、业务分类、厂家、Product、Lot、Wafer、Bin、参数、Cleaner 版本的服务端查询；
- 管理员增加用户筛选；
- 明细服务端分页/排序；
- BoxPlot、Histogram、Scatter、Correlation、Bin Pareto、Yield；
- Wafer Map 与 Heatmap 只在 Wafer/X/Y 可用时开放；
- 图表、明细和统计使用相同规范化 Filter Context；
- 参数超限只使用当前 Lot 唯一匹配 Spec。

退出条件：选择多个任务/Lot/Wafer 后，无需重新 Cleaner 即可重绘；浏览器不加载全表；图表计数、统计和明细在相同筛选下可对账。

PAT、Cpk、SPC 只有在算法口径和业务 Owner 批准后进入本阶段增量，不阻塞基础图表上线。

### A5：下载、重清洗和删除闭环

目标：完成三个高风险业务动作并证明互不干扰。

交付：

- `EXPORT_LATEST`：显示数据库 Cleaner 版本/最新版，临时生成并打包三个 Excel，不写 Canonical；
- 临时下载授权、完整性校验、TTL 清理；
- `REPROCESS_UPDATE`：新版本暂存、全量校验、原子 Current 切换和补录保留；
- 首次失败、导出失败、重清洗失败的独立错误处理；
- 普通用户删除本人任务、管理员删除任意任务；FTP 禁删保护；
- 同 SHA/同 Lot/不同用户和重复上传互不影响。

测试：

- BR-07～BR-09；
- 导出前后 Current ID、Canonical 行数和补录完全不变；
- 重清洗在 Cleaner 失败、导入中断和切换前异常时旧数据均可用；
- 成功后只有一个 Current；
- 删除 A 的任务不改变 B 的同 Lot 数据和 FTP 文件；
- TTL 清理不删除仍在下载或仍被引用的文件。

退出条件：三类动作均通过故障注入和权限测试，且可由普通业务用户在 UI 完成。

### A6：生产硬化与首版发布

目标：从功能可用提升到可维护、可恢复的内部生产版本。

交付：

- 用真实规模做查询、导入、图表和并发压测，决定 Worker=1 或 2 及必要索引；
- 数据库备份/恢复演练、FTP 不可用演练、Worker 重启演练；
- 管理员运行监控、失败任务重试、Cleaner Release 管理；
- 核心功能全部完成后再进行前端按页面加载和拆包优化；
- Windows Server 部署包、安装/升级/回滚说明、运维手册、用户手册；
- 华虹 CP + 日月新 FT 业务 UAT 和签字结果。

退出条件：BR-01～BR-10 全部通过；恢复、越权、故障注入、真实性能和安装环境 Smoke Test 通过；旧工具继续可用作为回退。

### A7：新增厂家滚动接入

每个厂家独立执行：真实样例 Profile → 原工具开发 Cleaner → 三 Excel 合同批准 → Release 登记 → Output Adapter → Golden Test → CP/FT 回归 → 管理员发布。不得为接入新格式放宽已批准格式，也不得在 TMS 复制 Cleaner 内部算法。

## 5. 需求到阶段的追踪矩阵

| 业务场景 | 主实现阶段 | 最终回归 |
|---|---|---|
| BR-01 华虹单 Lot | A2 | A6 |
| BR-02 多 Lot 相同 Spec | A2 | A4、A6 |
| BR-03 多 Lot 不同 Spec | A2 | A4、A6 |
| BR-04 缺 Lot_ID | A2 | A5、A6 |
| BR-05 缺 Product | A2/A3 | A6 |
| BR-06 不同用户同 Lot | A3 | A5、A6 |
| BR-07 最新 Cleaner 导出 | A5 | A6 |
| BR-08 重清洗更新 | A5 | A6 |
| BR-09 删除本人数据 | A5 | A6 |
| BR-10 管理员全权限 | A1 起贯穿 | A6 |

## 6. 测试分层

| 层级 | 重点 |
|---|---|
| Unit | 状态机、第一批次 Spec 选择、Effective Context、权限谓词、Manifest 校验 |
| Contract | 每个 Cleaner Release 的输入/输出 Manifest 和三个 Excel Schema |
| Integration | SQL Server migration、队列租约、Canonical 导入、Current 切换、删除事务 |
| Golden | 原 Cleaner 输出与数据库逐层对账，未知/相似格式拒绝 |
| API/UI | Owner/Admin 越权、上传、补录、查询、图表、导出、重清洗、删除 |
| Failure Injection | Cleaner 退出、输出缺失、磁盘满、FTP 中断、Worker 宕机、数据库中断 |
| Performance | 真实 Unit/Measurement 规模、2～8 用户、1～2 Worker、分页与图表聚合 |
| Release | Windows Server 安装、真实启动、升级、回滚、备份恢复 |

任何 Golden 对账不使用“看起来合理”作为通过标准。行数、身份、单位、Bin、Spec、统计等必须有批准的 expected 值；未知语义保持失败或 NULL。

## 7. 首批开发任务包

计划批准后，按以下顺序建立可评审的小 PR/提交：

1. A0 ADR 与现行三个 Excel 合同；
2. 数据库盘点脚本和 `analysis.*` 处置报告；
3. A1 forward migration 与数据库升级测试；
4. Cleaner Registry + 通用 Runner 合同测试；
5. SQL Job Queue Worker 与恢复测试；
6. 华虹 Output Adapter + Golden Import；
7. 自动 Current、缺失能力和补录；
8. 华虹查询与第一批图表；
9. 日月新 Adapter 与 Golden Import；
10. 导出、重清洗和删除三条独立闭环；
11. 生产硬化、UAT 和发布。

一个任务包只有在代码、migration、API/UI、测试和相关文档同时完成后才算完成。

## 8. 风险与控制

| 风险 | 影响 | 控制 |
|---|---|---|
| 三个 Excel 实际合同与当前描述不一致 | Writer 返工或错入库 | A0 用现行包和真实样例冻结 Sheet/列/类型 |
| 两套 Canonical 继续并存 | 查询和重清洗出现不同真相 | A1 前向迁移收敛，禁止新 Route B 写入 |
| Cleaner 内部调用不稳定 | 新厂家需要改 TMS | 标准 CLI/Manifest；厂家参数留在 Cleaner Release |
| 业务用户混入不同 Spec 批次 | 第一批次 Spec 不适用于其他批次 | 业务端只允许相同 Spec 批次一起比较；首版界面明确提示 |
| 重清洗破坏旧数据 | 历史分析不可用 | 新 Version 隔离写入 + 短事务 Current 切换 |
| Owner 授权遗漏 | 用户数据泄露 | 统一 Task Authorization + 每个对象的直接 URL 越权测试 |
| Measurement 查询变慢 | 图表不可用 | 服务端聚合、真实执行计划、按证据加索引 |
| FTP 原始文件不可取 | 无法导出/重清洗 | 任务页提前显示可用性；明确失败，不伪造结果 |

## 9. 时间和资源说明

本计划暂不承诺日历日期。开发速度主要取决于：现行三个 Excel 合同是否稳定、Golden 样例确认速度、真实数据规模、FTP 接入方式，以及当前代码与数据库已有数据的迁移情况。

建议一名主开发按 A0→A6 串行推进，业务 Owner 在 A0、A2、A3、A6 集中验收；如多人并行，只并行 UI、测试与相互独立的 Adapter，不并行设计第二套事实模型。

## 10. 开工门槛

满足以下条件后进入 A0/A1 实现：

- 用户确认 `TMS_Business_Requirements_v0.2.md` 为业务基线；
- 用户确认本架构采用 Route A、`test.*` 唯一事实源和内部自动 Current；
- 用户确认本开发阶段先华虹 CP、再日月新 FT；
- 用户确认维持工程/量产 × CP/FT 四个入口，入口直接确定数据类型；
- 提供或指定可用于 Golden 对账的现行 Cleaner 三个 Excel 样例；
- 允许只读盘点当前开发数据库中 `analysis.*` 的实际使用情况。
