# TMS 业务需求 v0.3：工程私有、量产共享与独立重复分析

- 确认日期：2026-08-30
- 状态：已确认，作为后续实现与验收的当前业务基线
- 适用范围：正式 CP/FT 上传、任务状态、结构化结果、Dataset Current、分析、质量摘要和重复上传
- 继承关系：未在本文改变的规则继续沿用 `TMS_Business_Requirements_v0.2.md`；本文与 v0.2 冲突时以本文为准
- 安全边界：本轮实现业务可见性和数据独立性；生产认证、安全运维、细粒度授权平台留到最后阶段

## 1. 已确认的三条核心规则

1. **工程数据按上传人隔离**：普通用户只能查询和查看自己上传的工程 CP/FT 数据。
2. **量产数据组织内共享读取**：所有已登录且具有 `DATASET_READ` 的业务用户，都可以查询和查看量产 CP/FT 的正式结果。
3. **量产允许重复上传并独立分析**：相同文件、相同 SHA、相同 Lot 可以由不同用户或同一用户重复上传；每次上传都形成独立 Batch、Receipt、Job、Analysis Run 和 Dataset，不覆盖、不合并、不使其他上传人的 Dataset 失效。

## 2. 最小可见性矩阵

| 对象或动作 | 工程数据 | 量产数据 |
|---|---|---|
| 上传记录和安全状态摘要 | Owner；系统管理员为维护例外 | 所有 `DATASET_READ` 用户 |
| Current + PUBLISHED 正式结果 | Owner；系统管理员为维护例外 | 所有 `DATASET_READ` 用户 |
| 图表、比较、明细 | 按正式结果读取范围 | 所有 `DATASET_READ` 用户 |
| 质量摘要 | 本人工程数据；管理员维护例外 | 全部量产正式数据 |
| 历史版本、Draft、失败处理中间事实 | Owner/Admin | Owner/Admin |
| 原始文件下载 | Owner/Admin | Owner/Admin |
| 补录、重处理、导出 Cleaner 文件、归档、删除 | Owner/Admin | Owner/Admin |
| Quick Analysis Workspace | Owner/Admin | 不因量产共享而自动放宽 |

说明：这里的“大家都可以查询和看到”指系统内具有正式数据读取权限的用户共享量产正式结果、图表和必要状态摘要，不等于任何人都能修改、重跑或下载他人的原始文件。

## 3. 重复上传的数据合同

### 3.1 必须独立的对象

每次上传必须独立创建：

- `ingestion.import_batch`；
- `ingestion.source_file_receipt`；
- `ingestion.processing_job`；
- `ingestion.processing_run`；
- `dataset.dataset`；
- 该 Dataset 自己的 Current Dataset Version。

### 3.2 可以复用的对象

- `ingestion.source_file` 可以按 SHA-256 复用，表示“文件内容相同”；
- 复用 Source 仅用于内容身份、血缘和存储治理，不代表上传任务相同，也不能作为覆盖、拒绝或跨 Dataset 换版的依据。

### 3.3 Current 语义

- Current 只在**同一个 Dataset**内切换；
- 同一 Batch 的显式重处理成功后，新 Version 成为该 Dataset 的 Current，旧 Version 变为 Superseded；
- 不同 Batch、不同 Owner、工程/量产之间，即使 Source SHA 相同，也必须各自保留 Current；
- 第二次分析失败时，任何既有正式 Dataset 均保持不变。

### 3.4 重复提示

- 系统可以显示“该内容以前上传过”；
- 提示必须同时说明“本次仍将创建独立分析”；
- 重复标记不得变成自动拒绝、自动覆盖、自动合并或自动复用分析结果。

## 4. 前端使用规则

- 工程入口明确显示“仅本人数据”；
- 量产入口明确显示“量产正式结果组织内共享”；
- 量产列表显示上传人、Batch、上传时间和重复来源标记，避免同 Lot 数据无法区分；
- 非 Owner 查看量产数据时，只显示可用的查询、图表和 Job 安全摘要，不显示补录、重新处理、归档等必然失败的按钮；
- 登录用户切换、退出或认证失效时，必须清空并取消前一用户的前端查询缓存，不能短暂展示上一用户的工程数据。

## 5. 性能与可用性门槛

- SHA-256 继续流式计算，不把大文件整体载入内存；
- 两名用户并发上传相同 SHA 时，两次上传均应成功，不因 Source 唯一键竞态失败；
- 工程/量产范围必须在 SQL 查询最前面过滤，禁止先查全量再在 Python 或前端过滤；
- Stage/Catalog 热查询目标不超过 3 秒；Chart/Detail 热查询目标不超过 3 秒；质量摘要热查询目标不超过 3 秒、冷查询候选不超过 5 秒；
- 性能结论必须记录真实开发库耗时和数据规模；没有执行计划或真实规模证据时，不凭猜测增加复杂缓存。

## 6. 必须验收的业务场景

1. 用户 A、B 各自上传工程数据：A 看不到 B 的列表、Dataset、图表、Job 和质量摘要。
2. 用户 A、B 各自上传量产数据：两人均能查询双方的正式 Current 和图表，但只能管理自己的上传。
3. A、B 顺序上传同一量产文件：1 个内容 Source、2 个 Receipt、2 个 Batch、2 个 Job、2 个 Dataset，两个 Dataset 均保持 Current + PUBLISHED。
4. A、B 并发上传同一 SHA：两次均成功，不出现唯一键冲突和孤儿上传目录。
5. 工程与量产上传同一 SHA：两个 Dataset 独立；量产共享规则不能反向暴露工程数据。
6. 同一 Batch 显式重处理：只切换本 Dataset 版本，不影响另一上传人的 Dataset。
7. 同一浏览器 A 退出、B 登录：前端不能显示 A 的工程缓存。

## 7. 本轮不扩建的事项

- 生产 SSO/MFA、网络边界、账号生命周期、密钥轮换和安全审计平台；
- Product/Project/Department 的复杂复合授权；
- 跨系统 SAP/MES/QMS 授权映射；
- 为重复输入自动复用旧分析结果；
- 大规模缓存平台、搜索引擎或提前建设分布式数据权限服务。
