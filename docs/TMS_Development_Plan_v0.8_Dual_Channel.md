# TMS 双通道开发计划 v0.8

> **执行基线更新（2026-08-29）**：本文保留双通道设计和 Quick Analysis 阶段证据；生产就绪收口以 [`TMS_Development_Plan_v0.9_Production_Readiness_Closure.md`](TMS_Development_Plan_v0.9_Production_Readiness_Closure.md) 为当前执行计划。本文 Q0 中“SQL Server 不可达”的描述是 2026-08-26 的历史状态，后续开发库闭环已经完成。

- 日期：2026-08-26
- 状态：历史阶段计划，增量工作并入 v0.9
- 正式入库基线：`TMS_Development_Plan_v0.7_Route_A.md`
- 快速分析业务合同：`business/TMS_Quick_Analysis_Business_Requirements_v0.1.md`
- 架构合同：`architecture/TMS_System_Architecture_v0.8_Dual_Channel.md`

## 1. 总体原则

v0.7 Route A 继续建设正式数据能力；v0.8 只新增隔离的快速分析和临时 Workspace 路线。两条路线共享认证、权限、Cleaner Registry、SQL 队列、Worker 和前端设计语言，但正式明细只允许写入 `test.*` Canonical。

## 2. 里程碑

### Q0：杰群 FT 快速 PAT（当前里程碑）

交付物：

- 受控 Source Catalog 和相对路径浏览；
- `workspace.analysis_session` 与 `QUICK_PAT` SQL 任务；
- 已发布 `ft_data_cleaner.pyz` 的杰群统一 CSV PAT Adapter；
- Manifest 前后双校验、Release SHA-256、结果 Artifact 和 TTL；
- 快速分析 API、页面、状态轮询和 PAT 下载；
- 真实 520 文件性能与一致性验证。

关闭条件：

- 单元测试、前端合同测试和生产构建通过；
- 真实 520 文件运行成功且公式逐项通过；
- SQL Server 2014 开发库升级到 `sql2014_0012`，API → SQL Queue → Worker → Result 全链通过；
- 快速任务前后 `test.*` 行数不变。

当前状态：代码、静态 Migration、测试、构建和非数据库真实 520 计算链已通过；SQL Server 网络不可达，在线 Migration 与 SQL 全链仍是 Q0 唯一开放门。

### Q1：临时交互 Workspace

- 将已批准清洗输出转换为 Parquet/Arrow；
- 提供按产品、Lot、参数、Bin 和结果筛选的临时查询；
- 支持散点图、分布图和多轮交互；
- 实施容量配额、TTL 清理、会话取消和运行日志；
- 不允许临时明细自动提升为 Canonical。

### Q2：企业存储适配

- 建立数据库化 Source Catalog 管理和管理员授权；
- 增加 SMB/NAS、FTP 和 SFTP Storage Adapter；
- 以 `source_id + relative_path` 替代存储协议差异；
- Worker 部署到数据附近，避免跨网络搬运多 GB 原始文件；
- 增加凭据托管、连接健康、访问审计和限流。

### Q3：Local Agent

- 为用户个人电脑提供签名、自动更新、最小权限的 Local Agent；
- Agent 在本地读取目录并调用同一已发布工具；
- 服务器只接收结果、Manifest、工具版本和运行证据；
- 建立设备注册、短期令牌、任务确认、断点恢复和撤销机制；
- 未完成安全评审前，不允许服务器远程任意浏览个人电脑。

### Q4：算法与厂家扩展

- 按已批准合同增加日月新、PowerTECH 和 CP 厂家；
- 增加良率、Fail Bin、Cpk/SPC 和图表类快速分析；
- 每种算法都要复用原工具、固定公式版本、使用 Golden 样例，并单独验收输入和输出合同。

## 3. 每个里程碑的强制门禁

- 真实输入、规模和运行环境已确认；
- 原始数据只读且不进入 Git；
- 未知厂家、格式、产品、参数布局、单位或算法语义失败关闭；
- 记录工具 Release、SHA-256、来源 Manifest、耗时、内存、临时磁盘和结果大小；
- 明确区分快速结果、临时 Workspace 与正式事实；
- 完成测试、构建、打包或运行时烟测，并形成日期化 Completion Report；
- SQL、FTP 或 Local Agent 等未在线验证的部分不得标记为完成。
