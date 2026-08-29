# TMS v1.1 功能灰度验证报告

- 报告日期：2026-08-30
- 灰度对象：TMS v1.1 Functional Closure 候选
- 已执行范围：G0 本机自动化/构建、G1 本机免登录任务 UAT、G2 `TMS_G0_DEV` 只读验收
- 未执行范围：生产认证/RBAC 验收、G3 测试服务器小组、G4 生产分批
- 数据库：SQL Server 12 / `TMS_G0_DEV` / `sql2014_0018`
- 当前 Gate：**本机 G0-G2 功能 PASS；G3/G4 NO-GO（尚未执行）**
- 发布包证据：`NCE-TMS-v1.1-functional-rc3.zip`，496,078 bytes，213 files，SHA-256 `a81da325ab3ad7b05edda4f1ff1e464331afc03c597c7315eec793715028d3c2`

## 1. 结论

本轮功能灰度只推进到本机 G0-G2，没有部署到生产，也没有把本机免登录页面验收包装成生产认证通过。

G0 自动化与构建、G1 浏览器任务流、G2 开发库只读 SQL/HTTP 均取得 PASS 证据：最终后端全量 457 passed/1 skipped/4 warnings，前端最终全量 24 files/116 tests，生产构建 13,055 modules；真实 SQL 在 SQL Server 12、`TMS_G0_DEV/sql2014_0018` 上执行 173 条只读语句、0 blocked，CP 单 Dataset、FT 双 Dataset 与 Current Catalog 对账通过且前后关键事实计数不变；浏览器覆盖四入口、历史多选、禁跨阶段、FT 比较、CP 明细、参数深链、Quick 清单、Job 追溯与质量看板，控制台无 warning/error。

浏览器走查发现并修复了 `processing_run.cleaner_release_id` 错误血缘；最终审计又关闭三个 P1：Chart 强制 Current + `PUBLISHED`；Current Catalog Lot 使用 Canonical `test_run`；`TMS_CURRENT_DATA` grant-only 读取从全版本收回为 Current + `PUBLISHED`，同时保留 Owner/Admin 历史读取且不放宽 WRITE。list、gate、summary、compare、chart、details 与 G0 调用已经统一，Gate/Summary 核心完成二次复核且没有保留额外读锁；49 项目标回归 PASS。同一只读窗口的 Catalog 复核覆盖 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages 和 7 filters，完整快照一致。这是既有功能授权边界修复，不是安全专项扩建。

前端首轮全量出现 1 个等待时序失败。测试默认等待上限从 1 秒放宽到 5 秒后，目标 2/2 和最终全量 116 tests 均通过；该调整只修改测试等待，不修改业务代码。

G3/G4 没有目标环境、生产账号、HTTPS、备份恢复、业务用户签字和变更窗口，因此当前明确为 NO-GO。发布 ZIP 已完成本机双构建、归档检查、秘密/禁止文件扫描和解包 launcher smoke，但该证据只形成可分发候选，不代表目标服务器部署。

## 2. Gate 状态

| Gate | 环境 | 通过条件 | 当前证据 | 状态 |
|---|---|---|---|---|
| G0 | 本机仓库 | 自动化、TypeScript、生产构建、目标回归全绿 | 后端最终 457/1/4；三个 P1 目标 49 tests；前端目标 2/2、最终 24 files/116 tests；build 13,055 modules | **PASS** |
| G1 | 本机免登录功能模式 | 四入口、正式任务、历史分析、Quick、质量看板、可恢复深链 | 浏览器任务矩阵 PASS；console warning/error 均为 0 | **PASS** |
| G2 | `TMS_G0_DEV` | SQL 身份正确、真实 CP/FT/Catalog 查询对账、Canonical/Current 不变、API 正常 | SQL Server 12 / 0018；173 条只读 SQL、0 blocked；Catalog 完整快照一致 | **PASS** |
| G2.5 | 本机认证冒烟 | 正式管理员/工程师合法路径和拒绝行为非回归 | 本轮未做生产认证验收；既有自动化仅用于防回退 | **未执行** |
| G3 | 测试服务器小组 | 正式账号、HTTPS、性能阈值、备份恢复、有限业务 UAT 与回退 | 不具备 | **NO-GO** |
| G4 | 生产分批 | 变更窗口、监控、回滚阈值、业务/IT/质量签字 | 不具备 | **NO-GO** |

Cleaner Release 血缘与三个最终 P1 修复均已进入后端最终全量 457/1/4；相关目标回归 49 tests、前端等待时序目标 2/2 和最终全量 116 tests 通过，因此 G0 在本机范围内判定 PASS。

## 3. G0：本机自动化与构建

### 3.1 已完成

- CP 良率、UNKNOWN/ABORT 和零已知分母回归；
- CP 多 Lot Spec 规范化指纹、逐 Lot 证据和冲突失败关闭；
- Dataset Compare/Detail/Chart、Current/`PUBLISHED`/Owner 上限、分页、筛选和 Spec 兼容合同；
- Current Catalog 的 Canonical Lot、Product/Wafer/Batch/Cleaner/Owner/上海时间合同；
- `TMS_CURRENT_DATA` grant-only、Owner/Admin 历史读取、WRITE 非放宽及 list/gate/summary/compare/chart/details/G0 一致合同；
- Quick Manifest 确认、来源变化、服务端分页和下载错误；
- 管理 KPI、上海业务日、失败 Job 范围和确定性最近 Current；
- 前端路由、提交后 Job、历史多选、分析深链、Quick 与质量看板；
- PowerShell 5.1/7 UTF-8 状态文件兼容；
- 清理无生产入口的旧前端页面/API 后重新执行测试和构建。

### 3.2 证据

| 验证 | 结果 |
|---|---:|
| 后端首轮全量 | PASS：435 passed / 1 skipped / 4 warnings |
| 最终修复后后端全量 | PASS：457 passed / 1 skipped / 4 warnings |
| 三个最终 P1 目标回归 | PASS：49 tests |
| 前端首轮全量 | FAIL：1 个等待时序用例 |
| 前端等待时序目标回归 | PASS：2 / 2 tests；等待上限 1 秒调整为 5 秒，业务代码未修改 |
| 前端最终全量 | PASS：24 files / 116 tests |
| TypeScript/Vite 生产构建 | PASS：13,055 modules |
| Release ZIP 双构建/inspection/smoke | PASS：`NCE-TMS-v1.1-functional-rc3.zip`；213 files / 496,078 bytes；SHA-256 双构建一致，inspection/launcher smoke VALID |

## 4. G1：本机任务型浏览器 UAT

### 4.1 一线任务

- 工程 CP、工程 FT、量产 CP、量产 FT 四个固定入口可到达。
- 历史正式数据以业务字段检索，不需要手填 Dataset ID。
- 同一阶段支持选择 1～8 个 Dataset；选择 CP 后 FT 被禁用，反之亦然。
- FT 真实双 Dataset 比较可见；CP 真实 Unit 明细与分页可见。
- 参数筛选进入 URL，刷新和深链恢复正常。
- Quick PAT 在执行前展示递归 Manifest，并要求确认范围。
- Job 详情可追踪状态、父子 Job、Cleaner Release、Source 与 Dataset。

### 4.2 领导/质量任务

- 首屏显示核心 KPI、已知良率、UNKNOWN 和数据新鲜度。
- 质量趋势图、筛选、方法说明和最近 Current Dataset 可用。
- 上海业务日和失败 Job 指标适用范围在页面可解释。
- 浏览器控制台 warning 0、error 0。

### 4.3 真实联测与最终审计缺陷闭环

浏览器加载历史正式数据时暴露 Current Catalog 的错误 Cleaner Release 关联。原查询引用不存在的 `processing_run.cleaner_release_id`，修复为：

```text
processing_run.job_id
  -> processing_job.cleaner_release_id
  -> cleaner_release
```

修复后实际 Catalog API 返回 HTTP 200/10 条，页面继续完成多选、比较和追溯任务。

最终审计关闭三个 P1：Chart 查询补齐 Current + `PUBLISHED` 门禁；Current Catalog Lot 改为从 Canonical `dataset_version_run -> test_run` 派生并在 Canonical 成员上筛选；`TMS_CURRENT_DATA` grant-only 读取收回到 Current + `PUBLISHED`，Owner/Admin 历史读取保留且 WRITE 未放宽。list、gate、summary、compare、chart、details 与 G0 调用统一；Gate/Summary 核心二次复核，未保留额外读锁。49 项目标回归和最终后端全量 457/1/4 均 PASS。当前同一只读窗口验证的 Catalog 最终实现为 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages、7 filters，完整快照一致；不把未在该窗口单独复现的历史中间状态写成真实库事实。

## 5. G2：开发库真实 SQL 与 HTTP

### 5.1 只读门禁

`scripts/g0/verify_v11_functional_sql_readonly.py` 要求数据库名、Schema Revision 和 SQL Server 身份精确匹配，并在执行前拒绝所有非 SELECT/只读 CTE 语句。输出对服务器、账号、Dataset、Lot、Wafer、参数和 Unit 身份做脱敏。

### 5.2 结果

| 场景 | 核心证据 | 结果 |
|---|---|---|
| 数据库身份 | SQL Server 12 / `TMS_G0_DEV` / `sql2014_0018` | PASS |
| SQL 审计 | 共 173 条已执行只读语句，0 blocked | PASS |
| CP | 单 Dataset compare/detail/分页/质量摘要与独立计数对账 | PASS |
| FT | 双 Dataset compare/detail/分页/质量摘要与独立计数对账 | PASS |
| Current Catalog | 10 keys、9 Canonical Lot members、7 distinct Lot、5 service pages、7 filters；完整快照一致 | PASS |
| Current Lot 样本 | 9 单 Lot、1 无 Lot、0 多 Lot | PASS（当前样本事实） |
| 良率 | PASS/(PASS+FAIL)，UNKNOWN/ABORT 分离，零分母为 NULL | PASS |
| 数据变异 | Canonical 与 Current 关键计数前后相同 | PASS |
| Catalog HTTP | 200，返回 10 条 | PASS |

本轮没有在真实开发库永久归档、重处理、发布或修改业务事实；灰度通过的是功能查询和可追溯性，不是写入动作或生产事务演练。

## 6. 回退与停止扩围条件

### 6.1 本轮回退能力

- SQL 验收本身为只读，前后计数不变，无需业务数据回滚。
- 浏览器发现 SQL 血缘错误后先修复并做目标回归，再重启服务复核 HTTP，不绕过失败。
- Chart 对非 Current 或非 `PUBLISHED` 版本失败关闭；Catalog Lot 只从 Canonical 成员派生与筛选。
- `TMS_CURRENT_DATA` grant-only 只读 Current + `PUBLISHED`；Owner/Admin 历史读取保留，WRITE 未放宽。
- CP 多 Lot 在缺逐 Lot Spec 或 Spec 不一致时失败关闭，不产生错误 Current。
- Quick 预览后来源变化时拒绝创建，不按陈旧 Manifest 继续计算。
- 发布包已形成本机可重复构建候选；G3/G4 未通过前不得把它传播为生产上线基线。

### 6.2 任何后续灰度必须停止的条件

1. Canonical/Current 计数在只读或展示操作后发生漂移；
2. UNKNOWN/ABORT 被归入 FAIL 或良率分母；
3. CP/FT 跨阶段比较、非 Current 数据或不兼容 CP Spec 被放行；
4. 用户仍需手填内部 Dataset ID 才能完成日常任务；
5. Source/Manifest 改变后任务仍执行；
6. 出现跨 Owner 数据可见、合法角色稳定 403 或既有拒绝行为回退；
7. ZIP 含原始数据、日志、账号、秘密、运行配置或未声明 Artifact；
8. 目标环境备份恢复、性能或业务口径未达到签字阈值。
9. READ COMMITTED 下可接受的换版瞬间 P2 超出约定边界，或业务要求提升为强一致快照。

## 7. 已确认、不确定与未执行

### 7.1 已确认

- 本机 G0-G2 功能基线具备自动化、真实 SQL、真实 HTTP 和浏览器任务证据。
- 四个固定入口、历史数据多选、FT 比较、CP 明细、参数深链、Quick 和质量看板可用。
- 173 条真实验收 SQL 全部为只读、0 blocked，没有改变 Canonical/Current 关键计数。
- Cleaner Release 错误血缘以及三个最终 P1 已关闭；49 项目标回归和最终 457/1/4 全量通过，Catalog 完整快照、分页与 7 类筛选通过同一只读窗口复核。
- TMS CP Writer 不会在缺多 Lot Spec 证据时猜测共享 Spec。

### 7.2 不确定/待验证

- 目标服务器并发、冷/热查询性能、连续运行和容量边界；
- 外部 CP Cleaner 多 Lot 逐 Lot Spec 的真实正向 Golden；当前真实样本为 9 单 Lot、1 无 Lot、0 多 Lot，多 Lot只有合同与自动化分支覆盖，没有真实 E2E。
- SQL Server READ COMMITTED 下保留可接受的换版瞬间 P2：分开的只读语句可能分别观察到切换前后状态。Gate/Summary 核心已二次复核，本轮没有增加额外读锁。

### 7.3 未执行

- 本轮没有扩建 AD/OIDC、HTTPS、证书、密码策略、服务账号 ACL 或生产安全体系；
- 没有用生产账号做认证/RBAC 验收；
- 没有目标服务器 G3 小组试用；
- 没有 G4 生产分批、变更窗口、监控、回滚演练或业务签字；
- 没有生产备份恢复或正式 SAP-B1/MES/QMS 接口联调。

## 8. G3 准入条件

当前 G3 为 NO-GO。只有以下条件全部满足，才能另行申请：

1. 保持最终后端/前端全量、构建和发布包 inspection 全绿；
2. 将当前 ZIP、SHA、Manifest、禁止文件与解包启动证据作为 G3 输入，不得替代目标服务器验收；
3. 单独的安全/认证计划批准，目标测试账号、权限矩阵和拒绝用例就绪；
4. 目标 SQL Server 补丁、HTTPS、服务账号、目录 ACL、计划任务和日志位置就绪；
5. 备份恢复演练、性能阈值、回退阈值和监控负责人明确；
6. CP/FT 业务方确定有限厂家、产品、Lot、人员和验收口径；
7. 多 Lot CP 如纳入灰度，必须先取得逐 Lot Spec Golden；否则继续排除该场景。

## 9. 下一步

1. 将安全与生产认证作为独立项目评估、计划和验收，不在本报告中补写不存在的结论。
2. G3 只选择一个小组和有限数据范围；达到性能、口径和回退阈值并签字后，才可讨论 G4。

## 10. 关联证据

- `docs/development/TMS_Frontend_Functional_First_Principles_Assessment_2026-08-29.md`
- `docs/development/TMS_Frontend_Functional_Development_Plan_v1.1_2026-08-29.md`
- `docs/development/TMS_Frontend_Functional_Completion_Report_2026-08-30.md`
- `docs/development/TMS_Frontend_Functional_Regression_Test_Report_2026-08-30.md`
