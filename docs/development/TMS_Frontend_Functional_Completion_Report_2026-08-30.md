# TMS v1.1 前端功能闭环完成报告

- 报告日期：2026-08-30
- 目标版本：TMS v1.1 Functional Closure
- 数据库：SQL Server 12 / `TMS_G0_DEV` / Alembic `sql2014_0018`
- 本轮范围：前端任务闭环、功能正确性、真实 SQL 只读对账、本机浏览器验收
- 安全边界：不扩建认证、安全基础设施或权限体系；既有认证、RBAC、Owner、Source Catalog、Manifest 与失败关闭边界不回退
- 功能交付结论：**本机 G0-G2 功能 PASS；生产认证验收、G3、G4 未执行**
- 发布包证据：`NCE-TMS-v1.1-functional-rc3.zip`，496,078 bytes，213 files，SHA-256 `a81da325ab3ad7b05edda4f1ff1e464331afc03c597c7315eec793715028d3c2`

## 1. 结论

本轮按照“第一性原理拆解 → 奥卡姆剃刀裁剪 → 计划冻结 → 开发 → 测试 → 开发库灰度”的顺序完成了 v1.1 本轮功能闭环。

第一性原理得到的核心需求不是继续增加页面，而是让两条最短业务链能够由用户直接走完：

1. 正式数据链：固定 CP/FT 入口 → 选择受控来源 → 确认 Manifest → 提交 → 自动进入 Job → 恢复/追溯 → 正式数据；
2. 历史分析链：按业务字段检索 → 选择 1～8 个同阶段 Current Dataset → 服务端比较/明细 → Job 与来源追溯。

本轮没有引入 Redux、微前端、通用图表设计器或新 UI 框架，也没有复活旧的手填内部 ID 页面；改造围绕既有 React Query、Dataset Current、Job 抽屉、Canonical 查询和 CP/FT 独立分析完成。浏览器任务型验收覆盖四个固定入口、历史多选、跨阶段阻止、FT 比较、CP 明细、参数深链、Quick 清单、Job 追溯和质量看板，控制台无 warning/error。

该结论只覆盖本机代码、构建、`TMS_G0_DEV` 只读对账和本机免登录功能灰度。它不是生产上线结论，也不代表本轮做过新的认证/RBAC 或生产安全验收。

## 2. 做了什么

### 2.1 功能正确性与运行闭环

- CP 良率统一为 `PASS / (PASS + FAIL)`；UNKNOWN/ABORT 不再进入 FAIL 或良率分母，已知分母为零时返回空值。
- 修复 PowerShell 5.1 与 PowerShell 7 对 UTF-8 状态文件的交叉读取，保留正常启动、状态检查和停止路径。
- 分析读取按 `DATASET_READ` 语义执行，避免合法查看角色因 `ANALYSIS_RUN` 语义错配而进入 403。
- 前端识别后端真实动作码 `REPROCESS_BATCH`，重新处理动作不再因模拟合同漂移被禁用。
- 根路由和工程/量产父路由按当前角色落到第一个可访问叶子页；显式访问无权页面仍保留拒绝行为。
- 统一 API 错误对象，保留 HTTP 状态、业务错误码、字段错误、可重试性和建议动作，前端可以给出下一步而不是只显示通用失败。
- 终审关闭 `TMS_CURRENT_DATA` 功能授权边界漂移：grant-only 调用只读 Current + `PUBLISHED`，Owner/Admin 继续保留历史读取，WRITE 权限没有放宽；list、gate、summary、compare、chart、details 与 G0 调用采用同一语义。

### 2.2 一线正式数据任务

- 四个固定入口继续保留：工程 CP、工程 FT、量产 CP、量产 FT；不增加自动类型猜测。
- 正式提交后自动打开返回的 Job，用户无需重新查找刚创建的任务。
- Stage 页的筛选、分页、Tab、Job 和分析上下文进入 URL；刷新、后退和深链可以恢复。
- 时间输入按 Asia/Shanghai 展示，提交后转换为 UTC，并采用左闭右开范围。
- Job 详情保留状态时间线、父子任务、Cleaner Release、来源 SHA、Dataset 和后续动作。
- Product 补录/修正进入当前“历史正式数据”主流程；人工业务有效值与 Cleaner 原值分离，不改写原始文件，并影响后续 Current 检索与管理汇总。

### 2.3 历史正式数据与分析

- `Dataset Current` 的用户入口改为“历史正式数据”，首屏使用产品、Lot、厂家、阶段、Cleaner、上传人和处理时间等业务字段。
- 增加 Product、Lot、Wafer、上传任务、Cleaner、Owner、厂家、业务域、阶段、状态和上海时间范围的服务端筛选与分页。
- 支持选择 1～8 个 Current Dataset；前端禁止混选 CP/FT，服务端重新校验数量、Current、权限和 Spec 兼容性。
- 顶层分析不再把手填 Dataset ID 作为主路径；分析从历史数据或任务结果上下文进入。
- 增加正式数据比较与结构化 Unit 明细接口，支持 Lot、Wafer、Bin、参数筛选和明细分页。
- FT 双 Dataset 比较和 CP 单 Dataset 明细已用真实开发库数据验证；参数筛选可以通过 URL 深链恢复。
- 比较口径保留 PASS、FAIL、UNKNOWN/ABORT 的分离；无已知分母时不伪造 0% 良率。
- 图表查询与比较/明细统一执行 Current + `PUBLISHED` 门禁，非当前或未发布版本返回 `ANALYSIS_VERSION_NOT_CURRENT`，不会绕过正式版本边界。
- Current Catalog 的 Lot 展示与检索改为来自 Canonical `dataset_version_run -> test_run`，不再把汇总行当作 Lot 身份来源。

### 2.4 CP 多 Lot Spec 边界

- TMS CP Writer 已增加规范化 Spec 指纹，比较参数名、单位、测试条件、LSL 和 USL。
- 多 Lot 必须取得逐 Lot 的 Spec Artifact 证据，且全部规范化指纹一致，才允许按共享 Spec 入库。
- 缺逐 Lot 证据、Lot 绑定歧义或任一 Spec 冲突时继续返回 `CP_MULTI_LOT_SPEC_BINDING_REQUIRED`，不会猜测或错误共享首个 Lot 的 Spec。

这完成了 TMS 侧的严格合同，但外部 CP Cleaner 当前仍缺“每个 Lot 都输出对应 Spec Artifact”的真实 Golden 证据。因此不能把多 Lot CP 端到端写成已经业务验收；当前正确状态是“具备严格接收能力，外部证据不足时失败关闭”。

本次同一只读验收窗口中的 10 个 Current Dataset 实际分布为 9 个单 Lot、1 个无 Lot、0 个多 Lot。因此，多 Lot 目前只有合同与自动化分支覆盖，没有真实多 Lot E2E 正向证据。

### 2.5 Quick PAT

- 运行前由后端 Source Catalog 构建递归 Manifest 预览，展示相对目录、文件数、总字节、文件类型和指纹。
- 创建任务必须回传已确认的 Manifest mode 与 SHA；预览后来源发生变化时返回 `QUICK_SOURCE_CHANGED`，不执行旧范围。
- Quick 历史改为服务端分页，并支持状态与日期筛选。
- 下载失败或 Artifact 过期会成为可见错误，提示刷新状态或重新发起任务。
- Quick Workspace 与正式 `test.*` Canonical 链继续隔离。

### 2.6 质量与领导视图

- 首屏优先展示正式 Current 数据量、已知良率、UNKNOWN 和数据新鲜度等决策指标。
- 质量趋势使用图表呈现；筛选和方法说明改为渐进披露。
- 趋势按 Asia/Shanghai 业务日归属，避免 UTC 日期跨日造成管理口径误读。
- 产品/Lot 筛选不适用于失败 Job 时，指标明确显示不适用，并在方法说明中公开范围，避免看似精确但范围不一致的数字。
- 最近 Current Dataset 查询采用确定性的最新结果与 Product 有效值，避免联接放大或旧结果覆盖。

### 2.7 功能债务收口

- 移除没有生产路由、已被替代的 `StageIntakeWorkbench`、`DatasetReview`、`JobWorkbench`、`HuaHongInspector` 和旧“能力中心”页面。
- 清理只为旧页面服务的前端 API、测试和样式；保留后端仍有正式用途的底层合同。
- 技术 ID、SHA、Release、Intent 和血缘保留在详情/追溯层，不再占据一线首屏。

## 3. 已确认的事实

### 3.1 自动化与构建

| 验证项 | 结果 | 说明 |
|---|---:|---|
| 后端首轮全量 pytest | **PASS：435 passed / 1 skipped / 4 warnings** | 首轮全量回归无失败 |
| 最终修复后后端全量 pytest | **PASS：457 passed / 1 skipped / 4 warnings** | Cleaner Release 血缘及三个最终 P1 修复均已纳入 |
| 功能授权边界目标回归 | **PASS：49 tests** | grant-only、Owner/Admin、WRITE 非放宽及 list/gate/summary/compare/chart/details/G0 一致性 |
| 前端 Vitest 最终全量 | **PASS：24 files / 116 tests** | 首轮有 1 个等待时序失败；默认等待由 1 秒调整为 5 秒后，目标 2/2 与最终全量通过，业务代码未因此修改 |
| 前端生产构建 | **PASS：13,055 modules** | TypeScript/Vite 构建完成 |
| 前端等待时序目标回归 | **PASS：2 / 2 tests** | 仅放宽测试等待上限，未修改业务实现 |
| Current Catalog 真实只读复核 | **PASS：10 keys / 9 Canonical Lot members / 7 distinct Lot / 5 service pages / 7 filters** | 完整快照一致；同一窗口样本为 9 单 Lot、1 无 Lot、0 多 Lot |
| Release ZIP 双构建与检查 | **PASS：213 files / 496,078 bytes** | `NCE-TMS-v1.1-functional-rc3.zip` 双构建 SHA-256 一致；CRC、Manifest、秘密/禁止文件扫描和解包 launcher smoke 均为 VALID |

浏览器真实走查发现 Current Catalog SQL 错把 `processing_run.cleaner_release_id` 当作血缘字段，实际合同是 `processing_run.job_id -> processing_job.cleaner_release_id -> cleaner_release`。最终审计关闭了三个 P1：图表补齐 Current + `PUBLISHED` 门禁；Catalog Lot 改用 Canonical `test_run`；`TMS_CURRENT_DATA` 从曾被扩大的全版本读取收回到 grant-only 仅 Current + `PUBLISHED`，同时保留 Owner/Admin 历史读取且不放宽 WRITE。list、gate、summary、compare、chart、details 与 G0 调用已经统一，并完成 Gate/Summary 核心二次复核；没有为了修复保留额外读锁。相关 49 项目标回归及最终后端全量 457/1/4 均通过。当前同一只读窗口进一步完成 Catalog 全量分页、7 类筛选和完整快照一致性复核。

### 3.2 真实 SQL 只读验收

| 项目 | 结果 |
|---|---|
| SQL Server 身份 | SQL Server 12 |
| 数据库 / Revision | `TMS_G0_DEV` / `sql2014_0018` |
| 校验入口 | `scripts/g0/verify_v11_functional_sql_readonly.py` |
| SQL 审计范围 | 173 条只读语句，0 blocked |
| CP 场景 | 单 Dataset 比较与明细 PASS |
| FT 场景 | 双 Dataset 比较 PASS |
| Current Catalog | 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages、7 filters；完整快照一致 |
| Current Lot 样本分布 | 9 单 Lot、1 无 Lot、0 多 Lot |
| 变异检查 | 验证前后关键事实计数不变 |
| 总结 | **PASS（只读）** |

该验收没有归档、重处理、发布或改写正式事实；因此可用于证明本轮查询合同和口径，但不能替代生产并发、性能或业务写入验收。

### 3.3 本机浏览器任务验收

| 场景 | 核心断言 | 结果 |
|---|---|---|
| 四个固定入口 | 工程/量产 × CP/FT 均可到达 | PASS |
| 历史多选 | 同阶段可选择；跨 CP/FT 明确阻止；最多 8 个 | PASS |
| FT 比较 | 真实双 Dataset 服务端比较可见 | PASS |
| CP 明细 | 真实 Unit 明细与分页可见 | PASS |
| 参数深链 | 参数筛选写入 URL，刷新后恢复 | PASS |
| Quick 清单 | Manifest 范围确认后才可创建任务 | PASS |
| Job 追溯 | 状态、父子任务、Cleaner 与来源链可见 | PASS |
| 质量看板 | 核心 KPI、趋势、说明和下钻可用 | PASS |
| 浏览器控制台 | warning 0 / error 0 | PASS |

## 4. 不确定的、延期的和未执行事项

1. **生产认证验收未执行。** 本轮按用户要求不扩建安全需求；没有新建 AD/OIDC、HTTPS、证书、服务账号、密码策略或生产权限矩阵，也没有以正式生产账号做认证/RBAC 验收。既有拒绝行为通过自动化保持非回归，但不能据此签发生产安全结论。
2. **CP 多 Lot 外部 Golden 未闭合。** 外部 CP Cleaner 仍需按 Lot 输出 Spec 证据并由真实样本证明一致；TMS 当前在证据不足时严格失败关闭。
3. **G3/G4 未执行。** 没有测试服务器小组试用、生产分批、正式并发/容量、业务签字和变更窗口。
4. **发布包不是生产部署。** ZIP 已通过可重复构建、归档检查和解包 launcher smoke，但仍只代表可分发候选，不代表目标服务器安装或生产运行通过。
5. **保留一个已接受的 P2。** 在 SQL Server 默认 READ COMMITTED 下，版本换版瞬间，分开的只读语句可能分别观察到切换前后状态；Gate/Summary 核心已二次复核且没有增加额外读锁，本轮接受该极短窗口，不把它写成已消除。

## 5. 完成判定

| 范围 | 判定 | 依据 |
|---|---|---|
| 第一性原理评估与奥卡姆裁剪 | 完成 | 评估、计划已冻结 |
| 一线正式任务闭环 | 完成 | 自动化与浏览器任务验收 PASS |
| 历史正式数据/比较/明细 | 完成 | CP 单 Dataset、FT 双 Dataset 真实只读 PASS |
| Quick/质量视图 | 完成 | 自动化与浏览器 UAT PASS |
| CP 多 Lot TMS 接收合同 | 实现完成 | 相同 Spec 需逐 Lot 证据；不同/缺证据失败关闭 |
| CP 多 Lot 外部端到端 Golden | 未完成 | 外部 Cleaner 尚缺逐 Lot Spec 证据 |
| 本机 G0-G2 功能灰度 | PASS | 自动化、构建、真实 SQL、浏览器、HTTP |
| 生产认证与 G3/G4 | 未执行 | 本轮明确范围外 |
| Release ZIP | PASS | `NCE-TMS-v1.1-functional-rc3.zip`；213 files；496,078 bytes；双构建与 launcher smoke PASS |

## 6. 下一步

1. 由 CP Cleaner Owner 补齐多 Lot 逐 Lot Spec Artifact，并用至少一组相同 Spec 正向 Golden 和一组不同 Spec 负向 Golden 完成业务验收。
2. 单独制定安全与认证计划后再执行 G2.5/生产角色验收；不要把本轮免登录功能 UAT 外推为生产权限通过。
3. 只有目标服务器、账号、HTTPS、备份恢复、性能阈值和业务签字齐备后，才能申请 G3；G3 通过后才能讨论 G4。

## 7. 关联文档

- `docs/development/TMS_Frontend_Functional_First_Principles_Assessment_2026-08-29.md`
- `docs/development/TMS_Frontend_Functional_Development_Plan_v1.1_2026-08-29.md`
- `docs/development/TMS_Frontend_Functional_Regression_Test_Report_2026-08-30.md`
- `docs/development/TMS_v1.1_Functional_Gray_Release_Report_2026-08-30.md`
- `docs/business/TMS_NCEpower_Business_Alignment_2026-08-30.md`
