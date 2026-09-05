# NCE PYMS FTP 对接开发测试完成报告

日期：2026-09-05。范围：用户明确启动的 FTP 服务器对接设计、前后端实现与测试。前置项目复审见 [项目复审报告](NCE_PYMS_Project_Review_Completion_Report_2026-09-05.md)，详细合同见 [FTP 集成设计](../architecture/NCE_PYMS_FTP_Integration_Design_2026-09-05.md)。

## 1. 完成结论

已完成可配置的 FTP/显式 FTPS 只读采集、前端数据源管理、Windows 凭据引用、独立采集 Worker、稳定性/完成标记检查、快照和 SHA、事务登记及检查点去重。通过真实本地 FTP、SQL Server 2014 开发库和实际运行的既有 Cleaner 验证，成功新增一个正式 FT Dataset。

企业 FTP 地址、实际协议、目录层级与只读账号尚未提供。本报告确认通用实现与本地开发环境验证完成，不代表企业 FTP 已接通、目标 Windows Server 已部署或 G3/G4 生产验收完成。

## 2. 需求与交付对照

| 业务需求 | 本次交付 | 验证情况 |
|---|---|---|
| 厂家服务器自动取数 | FTP/显式 FTPS、被动连接、UTF-8/GB18030、只读 MLSD/RETR | 真实本地 FTP 下载通过；FTPS 证书校验/保护数据通道通过单元测试 |
| CP/FT 固定厂家合同 | 阶段、厂家、数据域、已发布 Cleaner Release 固定；不识别猜测新格式 | 非法组合、未批准类型、华虹散落文本单文件模式被拒绝 |
| 避免读取上传中的数据 | 两次观察达到稳定窗口；目录包必须有明确完成标记；下载前后清单一致 | 本地协议测试、实际 Worker 等待窗口通过 |
| 避免重复入库 | 来源+路径唯一检查点；批次、回执、任务、检查点同一事务提交 | 重复扫描无新增批次/任务；SQL 注入故障完整回滚 |
| 数据归属可追溯 | DOMAIN 数据域、不可登录 SYSTEM_INGESTION 技术 Owner、来源与 SHA | Dataset 122、Batch 171、Job 258 对账通过 |
| 日常管理与失败处理 | 数据源中心新增配置、连接检查、启停、立即采集、采集记录、失败重试与任务跳转 | 前端组件/接口权限测试通过 |
| 运行连续性 | SQL 租约、续租、旧实例拒绝提交、重领中断记录、优雅停止 | 实库租约/暂停/重领测试及五进程启停通过 |
| Windows 部署 | 第五个本地进程；新增生产计划任务、凭据录入入口、生产就绪探针和外置配置示例 | 脚本测试、生产探针测试、发布包解包启动通过；未注册目标服务器任务 |

前端不接收密码。数据源管理权限本身不授予数据内容权限；普通用户仍按数据域授权查看。来源配置保存后默认暂停，修改远端目录或输入合同使用新的来源编码。

## 3. 实际数据链路证据

开发数据库为 `TMS_G0_DEV`，服务器 `WIN-0I8N01REB5K`，SQL Server `12.0.5000.0`，Schema `sql2014_0029`。

验收入口 [verify_ftp_collection_e2e.py](../../scripts/g0/verify_ftp_collection_e2e.py) 复制既有已成功处理的日月新 XLSX 到隔离测试 FTP 目录，使用临时生成的只读账号与本机凭据。实际受管 FTP Worker 下载并登记，实际 Route A Worker 调用 Cleaner Release 51 完成正式入库；未改原始样本。

| 项目 | 实际结果 |
|---|---|
| 输入大小 | 647,396 字节 |
| 输入 SHA256 | `eaf179dae60ee84cbd84a84d321fd4510e446393067bd855f13ec1cee81a9f73` |
| 来源 / 正式批次 / 清洗任务 / Dataset | 1 / 171 / 258 / 122 |
| 正式归属 | DOMAIN，数据域 4，技术 Owner 39 |
| 首次观察 | 1.375 秒，WAITING |
| 入库排队 | 34.297 秒，SUBMITTED / QUEUED |
| 清洗成功 | 49.719 秒，SUBMITTED / SUCCESS |
| 全部验收动作 | 54.11 秒，含 30 秒稳定窗口、重复扫描及源变化验证 |

重复扫描保持一个正式批次和一个任务。修改隔离测试副本后，记录变为 CHANGED，保留原任务，不自动重新入库。原样本 SHA 保持不变。该耗时仅是这一小样本的本地实测，不是企业网络、大批量吞吐或 SLA。

测试来源 1 已暂停，临时只读 FTP 服务已停止，临时凭据已移除；保留开发库验收批次、Dataset 和审计记录。API 提交使用隔离 TestClient 注入已有授权主体，持续运行的本地 API 保持登录认证；本轮不包含真实浏览器登录后的企业数据源操作验收。

## 4. 数据库与事务校验

[0029 迁移](../../db/alembic/versions/sql2014_0029_ftp_collection.py) 只增加 `ftp_collection_state`、`ftp_collection_run` 和 `ftp_package`。迁移前后原有五类事实计数与 Dataset 版本状态摘要完全一致：Test Run 273、CP Die 42,297、FT Device 999,091、CP Measurement 631,205、FT Measurement 18,409,406。

随后真实 FTP 验收正常增加 1 个 Test Run、2,989 个 FT Device 和 53,802 条 FT Measurement；CP 事实未变化。这是开发验收产生的新数据，不是迁移修改历史数据。

[verify_ftp_sql_guards.py](../../scripts/g0/verify_ftp_sql_guards.py) 在停止本地运行环境、所有已有 FTP 来源暂停时创建测试来源 2，验证：

- 同一来源只能由一个 Worker 领取，第二个 Worker 不能重复领取。
- 未达到稳定窗口不能提交采集，合格观察能够进入下载阶段。
- 在批次和回执已写入、创建任务前注入异常，整个事务回滚；批次、源台账、回执、任务、Dataset 和事实计数均不变。
- 错误、过期、已被接替的租约不能提交或续租；原执行记录变为 INTERRUPTED。
- 暂停后不能提交新任务；仅 SOURCE_ADMIN 或未获域授权的主体不能读取采集内容。

测试来源 2 最后保持暂停，未留下正式批次或任务。已存在 FTP 配置或历史记录时禁止自动降级 0029，避免丢失追溯。

## 5. 回归、构建与运行验证

| 验证 | 结果 |
|---|---|
| 后端及本地 Agent 全量：`python -m pytest -q tests local_agent/tests` | **1,309 passed，4 skipped，59.12 秒** |
| 前端全量：`npm test` | **60 个文件、296 项通过，800.80 秒** |
| 前端生产构建：`npm run build` | TypeScript 与 Vite 构建通过，26.27 秒 |
| 发布包 `2026.09.05-ftp` | 345 个文件，Schema 0029，manifest/路径/秘密扫描/解包 API 启动通过 |
| Windows 生产就绪测试 | FTP ready 缺失、数据库不符、进程已停止会拒绝通过；正确身份通过 |
| 本地五进程 | API、Vite、清洗、报告和 FTP Worker 均 ready，身份一致，`auth_required=true` |

4 项跳过沿用套件中的环境/可选能力条件，不计作通过。依赖的 openpyxl 弃用提示和 jsdom 样式提示仍存在。前端首屏包仍有既有大分块提示，主包约 2,388.92 kB（gzip 750.22 kB）；本轮没有把构建成功当作首屏性能验收。

本地证据位于被 Git 忽略的 `artifacts/runtime/`：`ftp-schema-verification.json`、`ftp-managed-e2e.json`、`ftp-sql-guards.json`、`ftp-backend-final.log`、`ftp-frontend-full.log`、`ftp-frontend-build.log`、`ftp-release-verification.log`。原始文件、运行配置、凭据、测试制品和日志不随源码提交。

## 6. 可复现入口

在已配置的开发环境，保留登录认证启动并核对五项就绪：

```powershell
.\scripts\windows\start_tms_local_test.ps1 -UseConfiguredAuthentication -NoBrowser
.\scripts\windows\get_tms_local_test_status.ps1 -AsJson -RequireReady
```

在当前 Windows 运行账号下录入实际 FTP 只读凭据，密码仅通过交互窗口输入：

```powershell
.\scripts\windows\set_tms_ftp_credential.ps1 -Reference FACTORY_FT
```

发布包环境增加 `-PythonPath` 指定包外 Python。生产配置需填写 `TMS_FTP_WORKER_ID`、`TMS_FTP_WORKER_READY_FILE` 和 `TMS_FTP_WORKER_STOP_FILE`，与正式清洗控制文件分开。完整步骤见 [Windows 部署指南](TMS_Windows_Runtime_Deployment_Guide.md)。

开发验收脚本只能用于指定开发库。真实入库验收会保留新的测试记录，事务守卫验收必须先停止本地受管进程；二者不可针对生产库运行。

## 7. 不确定项与下一步

1. 提供企业 FTP/FTPS 协议、地址/端口、根目录、编码、CP/FT 与厂家，以及单文件或目录批次规则；目录方式须明确完成标记及写入顺序。
2. 取得只读账号，在实际运行账号下录入凭据；验证 MLSD、远端 UTC 修改时间、FTPS 证书链与被动连接网络。实际 FTPS TLS 握手尚未在企业服务器验收。
3. 选取一个完整真实批次联调，对账 Lot、文件数、参数、Bin/良率语义和正式结果；CP 目录方式已通过协议/合同测试，尚无实际企业 CP FTP 全链路证据。
4. 验证实际规模的网络中断、重启、上传未完成、磁盘容量、长期定时运行及账号权限，完成目标服务器验收后再启用持续采集。

第一版要求完成后远端路径不可变；相同大小与修改时间下偷偷替换内容无法靠 MLSD 元数据发现。中断恢复按包/任务检查点执行，不提供字节级续传。提交响应不明确时保留快照，未登记快照需对账后清理。实际服务器不支持 MLSD 或使用特殊目录合同，需基于样本增加专用适配，不能降级为猜测解析。

本次实现复用了成熟正式入库与 Cleaner 链路，实库证明去重和事务边界有效。剩余工作集中在企业端合同、连接与运行验收；SAP、异常工单和 AI 的既有暂缓范围未扩展。
