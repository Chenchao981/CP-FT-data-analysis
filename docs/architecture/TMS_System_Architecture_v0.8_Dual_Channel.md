# TMS 系统架构 v0.8：正式数据与快速分析双通道

- 状态：P0 实施基线
- 日期：2026-08-26
- 继承：`TMS_System_Architecture_v0.7_Route_A.md`

## 1. 架构结论

v0.7 Route A 继续负责正式入库，并保持唯一 Canonical 事实链。v0.8 新增隔离的 `workspace` 边界处理一次性或临时分析。两条通道共享登录权限、Cleaner Registry、SQL 任务队列、Worker 运行框架和前端状态体验，但不共享明细事实存储语义。

```text
浏览器
  ├─ 正式入库 ──> Upload/Storage Adapter ──> INITIAL_IMPORT ──> Cleaner ──> test.* Canonical
  └─ 快速分析 ──> Source Catalog ──> QUICK_PAT ──> 已发布 PAT 工具 ──> Result + Manifest
                                      │
                                      └─ workspace.analysis_session（只存控制面和摘要）
```

## 2. 组件职责

### 2.1 Source Catalog

- 从部署配置加载数据源代码、显示名称、真实根路径、阶段、厂家和允许扩展名。
- 对外仅返回数据源代码、显示名称、可用状态和相对目录。
- 统一完成路径归一化、根目录包含性检查、目录浏览和来源 Manifest。
- P0 支持 Windows 本地盘或已挂载共享目录；FTP/SFTP 通过后续 Storage Adapter 接入。

### 2.2 Quick Analysis API

- 校验 `ANALYSIS_RUN` 权限。
- 根据 `source_root_code + relative_path` 创建会话。
- 固定从 Cleaner Registry 选择 `FT/JIEQUN` 已发布 PAT Release。
- 写入 `workspace.analysis_session`，再创建引用该会话的 `QUICK_PAT` SQL 任务。
- 提供会话列表、详情、目录浏览和受权结果下载。

### 2.3 SQL Queue

`ingestion.processing_job` 新增可空 `analysis_session_id`。任务输入必须且只能是以下一种：

- `source_file_id`；
- `import_batch_id`；
- `analysis_session_id`。

`QUICK_PAT` 与 `INITIAL_IMPORT` 使用同一套 Claim、Lease、Heartbeat、Retry 和终态机制，避免建设第二套不可靠队列。

### 2.4 Workspace Control Plane

`workspace.analysis_session` 只保存：

- 输入身份和 Manifest；
- 用户、工具 Release、状态和生命周期；
- 文件/字节/参数等摘要；
- 结果摘要和错误。

PAT Excel、摘要 JSON 和 Manifest JSON 继续登记到 `ingestion.processing_artifact`。P0 不在 `workspace` 或 `analysis` 建立逐颗器件明细表。

### 2.5 Quick PAT Worker Adapter

- 根据会话重新解析受控目录，不信任数据库中的绝对路径。
- 重算并比对 Manifest，阻止排队期间的数据漂移。
- 校验已发布 PYZ 的 SHA-256。
- 在独立 `job/attempt` 工作目录中启动子进程。
- 子进程只允许执行杰群统一 CSV 原始目录 PAT 入口。
- 验证唯一 PAT Excel、摘要 JSON 和 Manifest JSON 后登记 Artifact。

## 3. 数据流与状态

```text
CREATE SESSION(QUEUED)
  -> CREATE JOB(QUEUED)
  -> CLAIM JOB(RUNNING)
  -> SESSION(RUNNING)
  -> SOURCE/MANIFEST/RELEASE CHECK
  -> PAT SUBPROCESS
  -> ARTIFACTS + SUMMARY
  -> SESSION(SUCCESS)
  -> JOB(SUCCESS)
```

任何校验或计算异常都会将会话和任务置为失败，并保留可审计错误；不会向 `test.*` 写入数据。

## 4. 配置合同

P0 使用 `TMS_SOURCE_ROOTS_JSON`：

```json
[
  {
    "code": "JIEQUN_FT_SHARED",
    "name": "杰群 FT 共享数据",
    "path": "F:\\shared\\ft\\jiequn",
    "test_stage": "FT",
    "factory_code": "JIEQUN",
    "allowed_suffixes": [".csv"]
  }
]
```

结果目录由 `TMS_QUICK_WORK_ROOT` 指定；默认保留时长由 `TMS_QUICK_RESULT_TTL_HOURS` 指定。账号、密码和 FTP 凭据不得放入该 JSON 或数据库结果中。

## 5. 扩展路径

- 把 Source Catalog 的本地文件实现抽象为 Storage Adapter 后，可加入 SMB、FTP 和 SFTP，而 API 合同保持不变。
- 增加 Local Agent 时，由 Agent 在本机执行已签名工具包；服务器接收结果、工具版本和签名 Manifest，不获得任意浏览本机目录的能力。
- 临时交互分析使用单独的 Parquet/Arrow Workspace 与 TTL，不把临时明细混入 Canonical。

## 6. 明确不做

- 不让浏览器直接把用户电脑路径交给服务器读取。
- 不把任意绝对路径作为 API 参数。
- 不为快速 PAT 创建 681 万行以上的正式测量记录。
- 不复制或重写杰群解析、单位换算和 PAT 公式。
- 不自动把快速结果提升为正式数据。
