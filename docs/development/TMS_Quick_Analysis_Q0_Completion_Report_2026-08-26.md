# TMS Quick Analysis Q0 Completion Report

- 日期：2026-08-26
- 里程碑：Q0 杰群 FT 原始目录快速 PAT
- 结论：**代码、真实计算链和 `TMS_G0_DEV` SQL 全链全部通过；Q0 开发库状态为 PASS**
- 部署边界：本报告关闭开发库验收门；生产环境发布、过期文件物理清理和容量配额仍属于后续工作

## 1. 本次完成内容

### 1.1 业务与架构合同

- 新增快速计算、临时 Workspace、正式入库三种语义边界。
- 固定快速结果不能自动或静默提升为正式 Canonical。
- 固定浏览器不能直接浏览用户个人电脑；P0 只允许管理员配置的服务器受控根目录。
- 形成 Quick Analysis v0.1 业务需求、v0.8 双通道架构和 v0.8 开发计划。

### 1.2 数据库与任务模型

- 新增 Alembic head `sql2014_0012`。
- 新增 `workspace.analysis_session`，只保存用户、来源、Manifest、Release、状态、摘要和 TTL，不保存逐颗器件测量明细。
- `ingestion.processing_job` 新增 `analysis_session_id` 和 `QUICK_PAT`；数据库 Check 强制一个任务只能引用 `source_file_id`、`import_batch_id`、`analysis_session_id` 三者之一。
- 继续复用现有 SQL Claim、Lease、Heartbeat、Retry 和 Artifact 表，不建设第二套队列。
- Cleaner Bootstrap 新增 `FT/JIEQUN/JIEQUN_FT_QUICK_PAT_PYZ` 已发布合同。

### 1.3 受控来源与安全

- 新增 Source Catalog，部署配置为 `TMS_SOURCE_ROOTS_JSON`。
- API 只接收数据源代码和相对路径，不返回真实服务器根路径。
- 拒绝绝对路径、盘符、UNC、`..` 和根目录逃逸。
- Manifest 使用 `PATH_SIZE_MTIME_V1`，记录每个 CSV 的相对路径、大小和 `mtime_ns`。
- Worker 在计算前和计算后各重算一次 Manifest；排队期间或计算期间发生增删改即失败。
- 原始目录只读，临时 spool 和结果均位于独立 `job/attempt` 工作目录。

### 1.4 已发布 PAT 工具复用

- TMS 没有重写杰群 CSV Parser、格式检测、单位换算、参数选择、四分位数或控制限公式。
- Worker 子进程直接调用已发布 `ft_data_cleaner.pyz` 中的 `factories.jiequn.pat_cleaner.generate_raw_pat`。
- 执行前校验 PYZ SHA-256；P0 只接受已验收的杰群统一 CSV 格式。
- 工具检测文件数必须等于 Manifest 文件数，工具参数数必须等于 PAT Excel 参数数。
- 结果登记为 `pat_report`、`pat_summary` 和 `source_manifest` 三类 Artifact。

### 1.5 API 与前端

- 新增数据源列表、目录浏览、创建 PAT、会话列表/详情和结果下载 API。
- 新增“快速分析”独立菜单。
- 用户可逐级浏览相对目录、用当前目录提交任务、查看排队/计算/成功/失败/过期状态，并下载 PAT。
- 页面明确提示“不上传原始文件、不写入正式 Canonical、默认结果 7 天过期”。

## 2. 真实 520 文件验证

### 2.1 输入与工具事实

| 项目 | 实测值 |
|---|---:|
| 来源文件 | 520 个 CSV |
| 来源总大小 | 3,041,085,645 bytes（约 2.83 GiB） |
| Manifest 生成耗时 | 0.187853 秒 |
| Manifest SHA-256 | `55b9b0e951fc9db8a4f29656509d467c78389025a176e65ac963eac923785355` |
| 已发布 PYZ SHA-256 | `768ae7c4709eb7aa18c0d7f9846ccd57c58520986dc2f3e59c17ff83df19390f` |
| 工具解析行数 | 6,813,800 |
| PAT 参数数 | 23 |

Manifest 只读取目录元数据，没有顺序读取或上传 2.83 GiB 文件内容。快速任务因此把“提交前准备”缩短为约 0.19 秒的目录扫描；PAT 本身仍在数据所在机器执行完整精确计算。

### 2.2 性能与资源

| 项目 | 最终验收实测值 |
|---|---:|
| PAT 计算耗时 | 98.221 秒 |
| 监控墙钟时间 | 98.207 秒 |
| 子进程峰值 RSS | 378,019,840 bytes（约 361 MiB） |
| 工作目录峰值 | 1,181,754,848 bytes（约 1.10 GiB） |
| 完成后工作目录 | 95,806 bytes（约 93.6 KiB） |
| PAT Excel | 7,759 bytes |

临时磁盘峰值来自按参数流式写入的 `float64` spool；参数完成后临时文件被删除。系统没有生成巨型清洗 Excel，也没有把 681 万行写入 TMS 数据库。

### 2.3 结果一致性

- 23 个参数均验证：`Sigma=(Q3-Q1)/1.35`，`LCL/UCL=Median±6Sigma`；结果 **23/23 PASS**。
- 两次独立成功运行的 PAT Excel 为 25 行 × 17 列，逐单元格完全一致。
- 两个 XLSX 文件 SHA-256 不同，原因是工作簿元数据时间不同；业务单元格值相同，因此不把文件字节哈希当作统计一致性的唯一证据。
- 首次验证暴露并修复了“总解析行数”与“最大参数有效值数”的语义混淆：最终会话 `record_count=6,813,800` 来自工具运行摘要；最大参数有效值数 6,812,086 单独保留在结果摘要中。
- 一次重跑曾由性能监控器读取正在删除的 spool 文件而中断；已修复监控脚本的临时文件竞态，随后完整重跑通过。生产 PAT Runner 不含该监控竞态。

### 2.4 SQL Queue 全链验证

网络恢复后在 `TMS_G0_DEV` 执行了真实520文件在线验收：

| 项目 | 实测值 |
|---|---:|
| 数据库升级 | `sql2014_0011 -> sql2014_0012` |
| Schema 验证 | 25 schemas、55 tables、4 views，PASS |
| JIEQUN Quick PAT Release | `cleaner_release_id=15` |
| Analysis Session / Job | `1 / 42` |
| SQL 全链墙钟时间 | 125.104 秒 |
| 在线 Manifest SHA-256 | `2fd9688b5447d5a61a8976e34156da9415c79139a778005ab907b64893ba7054` |
| PAT Result SHA-256 | `9649e6b159d65f1c629b8d331b26d6031cac4c4d4dd7bd40781d3337de28d55b` |
| Artifact | `pat_report`、`pat_summary`、`source_manifest` |

在线 Manifest 使用受控根代码和相对目录 `520data`，因此与非数据库验收时的 Manifest 哈希不同；两次输入文件数和总字节数完全相同。API 创建、SQL Claim、30秒心跳续租、Worker、结果登记和 Excel 下载均通过。

普通分析用户访问管理员创建的会话详情、结果下载和列表均得到 404，任务查询也被所有权条件拒绝。正式事实表前后计数如下：

| 正式事实表 | 运行前 | 运行后 | 增量 |
|---|---:|---:|---:|
| `test.test_run` | 77 | 77 | 0 |
| `test.unit_result` | 92,335 | 92,335 | 0 |
| `test.measurement` | 1,588,741 | 1,588,741 | 0 |

## 3. 自动化验证

| 验证 | 结果 |
|---|---|
| 后端 Unit/API/Worker/Migration 静态测试 | 103 passed |
| Python 变更文件 Ruff | PASS |
| 前端 API 合同测试 | 16 passed（6 files） |
| TypeScript + Vite 生产构建 | PASS，29.68 秒 |
| Git whitespace 检查 | PASS |
| Alembic 单一 head | `sql2014_0012` PASS |
| 真实 520 非数据库 Quick PAT 链 | PASS |
| `TMS_G0_DEV` 在线 Schema 验证 | PASS |
| 真实 520 API → SQL Queue → Worker → Download | PASS，125.104 秒 |
| 普通用户所有权隔离 | PASS |
| Quick PAT 前后 `test.*` 零增长 | PASS |

测试期间有 4 条来自 `openpyxl` 的 `datetime.utcnow()` DeprecationWarning；不影响当前结果，依赖升级时需要复验。Vite 仍提示既有主包和 Analytics chunk 大于 500 kB；Quick Analysis 自身已懒加载，压缩前约 10.51 kB。

## 4. 做得好的部分

- 用 Adapter 复用已发布工具，TMS 没有复制成熟的厂家规则和 PAT 算法。
- Quick Analysis 的控制面与正式事实完全分离，同时复用了可靠 SQL 队列基础设施。
- 来源路径从任意绝对路径收紧为管理员根目录和相对路径。
- Manifest 在计算前后双校验，避免“提交时一批数据、完成时另一批数据”。
- 真实大目录验证记录了时间、内存、临时磁盘和结果语义，而不是只凭小样例判断性能。
- 发现“参数有效值数不等于解析行数”后修正数据合同，没有用看似合理的数字掩盖语义差异。

## 5. 不确定性与薄弱点

### 5.1 部署边界

- `TMS_G0_DEV` 在线门已经关闭；生产环境尚未执行 Migration、数据源配置和 Worker 服务化部署。
- 本次使用真实 JWT、SQL `auth_session` 和 RBAC 依赖验证接口，但没有重置或使用用户密码，因此不重复验证登录密码流程。
- SQL E2E 保留 Session、Job 和三个临时 Artifact 作为开发库验收记录；结果按7天 TTL 过期。

### 5.2 当前 P0 限制

- 只支持杰群统一 CSV 原始目录 PAT；其他杰群布局、日月新、PowerTECH 和 CP 尚未开放。
- Source Catalog 当前由环境变量配置；尚无数据库管理页面、FTP/SFTP Adapter 或凭据托管。
- `PATH_SIZE_MTIME_V1` 不逐文件计算内容哈希；它降低了提交延迟，但不能替代正式长期审计。
- 结果到期后 API 拒绝下载并显示过期；物理文件定期清理任务尚未实现。
- 当前只是服务器/挂载目录方案；用户个人电脑仍需后续受认证 Local Agent。
- 没有实跑“520 文件上传 + 正式 Canonical + PAT”的旧系统全链，因此不能给出整体加速百分比。可以确认的是，新链路在设计上完全取消了原始文件上传和 681 万行正式明细写入这两个前置步骤。

## 6. 下一步

1. ~~实现过期 Artifact 物理清理、容量配额和清理审计，补齐结果生命周期。~~ 已于2026-08-27完成，见 `TMS_Quick_Workspace_Lifecycle_Q0_1_Completion_Report_2026-08-27.md`。
2. 将 API 与 Worker 配置为 Windows 服务，验证重启、断网续跑和运行日志轮转。
3. 生产发布前单独审批数据库目标、Source Catalog、服务账号权限和备份/回滚窗口。
4. 进入 Q1 Parquet/Arrow 临时交互 Workspace，继续保持与正式 Canonical 隔离。
