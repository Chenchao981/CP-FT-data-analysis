# TMS Backend

> 正式数据执行主线为 Route A；一次性PAT使用隔离的Quick Analysis Workspace。两条通道共享SQL队列和Worker，但只有正式导入写入Canonical。

当前仓库唯一 Alembic head 为 `sql2014_0025`。开发库是否已升级必须在线核对数据库、服务器和 Revision；其他环境不能根据仓库文件名推断已升级。

## 开发环境

```powershell
conda create -p .conda-env python=3.12 alembic sqlalchemy pyodbc fastapi uvicorn pytest httpx
$env:PYTHONPATH = "$PWD\backend"
```

数据库连接通过进程环境变量 `TMS_DATABASE_URL` 注入，不写入仓库。

## 启动

完整本机功能验收优先使用仓库根目录的一键入口，它会加载 SQL 运行配置并同时管理 API、Worker 和前端：

```powershell
.\启动TMS测试环境.bat
```

下面的命令只适用于分别调试单个后端进程。

```powershell
.\scripts\windows\run_tms_api.ps1
```

另开一个 PowerShell 窗口启动 Route A Worker：

```powershell
.\scripts\windows\run_tms_worker.ps1
```

Windows PowerShell 5.1 必须通过 `scripts/windows/TmsRuntime.Common.ps1` 显式按 UTF-8 加载 `.env.runtime.ps1`；不要在包含中文路径的运行配置上直接 dot-source。生产计划任务使用 `run_tms_api.ps1` 和 `run_tms_worker.ps1`，本机完整测试使用 `start_tms_local_test.ps1`。

部署新环境时，先升级 Migration，再按实际发布包 SHA256 幂等登记 Cleaner：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\Scripts\alembic.exe -c db\alembic\alembic.ini upgrade head
& .\.conda-env\python.exe scripts\g0\bootstrap_existing_cleaner_releases.py --all

# 新厂家/新包应按厂家单独登记，避免把其他已验收路由静默切到当前共享包：
& .\.conda-env\python.exe scripts\g0\bootstrap_existing_cleaner_releases.py --factory DIANJI
```

接口：

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `POST /api/v1/contracts/format-profiles/validate`
- `POST /api/v1/contracts/cleaner-releases/validate`
- `GET /api/v1/contracts/cleaner-adapters`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/details`
- `POST /api/v1/jobs/{job_id}/transitions`
- `POST /api/v1/cleaners/huahong/inspect`
- `POST /api/v1/datasets`
- `POST /api/v1/datasets/{dataset_id}/versions`
- `GET /api/v1/datasets/{dataset_id}/versions/{version_no}/gate`
- `POST /api/v1/datasets/{dataset_id}/versions/{version_no}/publish`
- `GET /api/v1/datasets/{dataset_id}/versions/{version_no}/summary`
- `GET /api/v1/datasets/{dataset_id}/versions/{version_no}/charts`
- `GET /api/v1/catalog/datasets/current`
- `GET /api/v1/{engineering|production}/{cp|ft}/uploads/page`
- `GET /api/v1/{engineering|production}/{cp|ft}/results/page`
- `GET /api/v1/{engineering|production}/{cp|ft}/source-roots`
- `GET /api/v1/{engineering|production}/{cp|ft}/source-roots/{root_code}/manifest-preview`
- `POST /api/v1/enrichments`
- `GET /api/v1/enrichments/batches/{import_batch_id}`
- `GET /api/v1/enrichments/fields/{CP|FT}`
- `GET /api/v1/quick-analysis/source-roots`
- `GET /api/v1/quick-analysis/source-roots/{root_code}/directories`
- `POST /api/v1/quick-analysis/pat`
- `GET /api/v1/quick-analysis/sessions`
- `GET /api/v1/quick-analysis/sessions/{analysis_session_id}`
- `GET /api/v1/quick-analysis/sessions/{analysis_session_id}/download`
- `GET /api/v1/management/quality-summary?access_scope=PERSONAL`
- `GET /api/v1/management/quality-summary?access_scope=DOMAIN&data_domain_id={id}`
- `GET /api/v1/master-data/product-crosswalks`
- `POST /api/v1/master-data/product-crosswalks/{crosswalk_id}/{approve|reject}`
- `GET /api/v1/operations/consistency`
- `GET /api/v1/operations/workers`
- `POST /api/v1/operations/workers/{worker_id}/{drain|resume}`
- `POST /api/v1/lifecycle/exports`
- `GET /api/v1/lifecycle/exports/{job_id}`
- `GET /api/v1/lifecycle/exports/{job_id}/artifacts/{artifact_id}/download`
- `POST /api/v1/lifecycle/datasets/{dataset_id}/archive`
- `POST /api/v1/lifecycle/datasets/{dataset_id}/reprocess`
- `/api/docs`

Job Service默认使用进程内实现；Route A 联调和部署必须设置 `TMS_JOB_REPOSITORY=sql`。SQL Repository支持原子领取、租约、心跳、超时恢复、幂等键和最大重试次数。

正式入库使用 `INITIAL_IMPORT + ATOMIC_V1` staged/finalize 合同。Writer完成准备态事实、Dataset Version、全部来源映射和 finalize intent 后，服务在一个事务中切换 Run/Dataset Current、结果摘要、Batch、Job 和 intent。租约中断可以直接重放 staged intent，不重复运行Cleaner。

Quick Analysis通过`TMS_SOURCE_ROOTS_JSON`配置管理员受控根目录。浏览器和API只使用`source_root_code + relative_path`，不接受任意绝对路径。P0仅支持杰群统一CSV目录PAT；结果写入`TMS_QUICK_WORK_ROOT`并按`TMS_QUICK_RESULT_TTL_HOURS`登记过期时间。

Local Agent 结果上传中断后遗留的`.staging/<uuid>`目录默认24小时后在下一次结果接收时回收，可用`TMS_LOCAL_RESULT_STAGING_TTL_SECONDS`调整为60秒至7天。回收只处理精确32位十六进制UUID目录，发现符号链接、junction或reparse point时不会递归删除。

Quick Analysis容量按“活跃任务预留 + 失败任务未清理预留 + 尚未清理Artifact”计算。默认按源文件总字节数的50%加64 MiB预留临时空间，并同时检查全局容量、单用户容量和工作盘最小剩余空间。部署时可通过`TMS_QUICK_GLOBAL_CAPACITY_BYTES`、`TMS_QUICK_USER_CAPACITY_BYTES`、`TMS_QUICK_MIN_FREE_BYTES`、`TMS_QUICK_RESERVE_RATIO`和`TMS_QUICK_RESERVE_OVERHEAD_BYTES`调整。

过期结果清理先用只读模式确认范围，再执行物理删除：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\python.exe scripts\run_quick_artifact_cleanup.py --dry-run
& .\.conda-env\python.exe scripts\run_quick_artifact_cleanup.py
```

清理器只允许删除`TMS_QUICK_WORK_ROOT/<job_id>`这个精确子目录，遇到目录逃逸或符号链接会标记`BLOCKED`。Session、Job、Manifest、SHA和`governance.audit_log`记录不会删除。进程中断后停留在`CLEANING`的任务默认30分钟后允许恢复，可用`TMS_QUICK_CLEANUP_STALE_MINUTES`调整。

正式导出 Artifact 使用独立 Formal Cleanup，不与 Quick Workspace 混用。先以 DryRun 预览：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\python.exe scripts\run_formal_artifact_cleanup.py --dry-run
```

只有显式审批后才使用 `--delete`。Formal Cleanup 只处理 `TMS_WORK_ROOT/<job_id>` 规范直接子目录，并再次检查 Job 终态、TTL、lease、永久 Artifact、路径逃逸与 reparse point；数据库审计和业务事实不删除。

开发库可用小型合成过期文件复验完整清理链；脚本完成后会移除全部合成记录：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\python.exe scripts\g0\verify_quick_cleanup_sql_e2e.py
```

真实520文件的非数据库计算链验证：

```powershell
& .\.conda-env\python.exe scripts\g0\verify_quick_pat_e2e.py `
  --source-root 'F:\共享数据\FT\杰群' `
  --relative-path '520data' `
  --output-root 'F:\CP-FT数据分析\artifacts\quick-pat-e2e'
```

真实开发库的API、SQL Queue、Worker、Artifact下载、所有权隔离与正式事实零增长验证：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\python.exe scripts\g0\verify_quick_pat_sql_e2e.py `
  --source-root 'JIEQUN_FT_SHARED' `
  --relative-path '520data'
```

脚本要求开发库存在一个系统管理员和一个具备`ANALYSIS_RUN`的普通用户。它保留成功的Quick Analysis会话、任务与临时Artifact作为验收记录，但不会向`test.test_run`、`test.unit_result`或`test.measurement`写入数据。

华虹文件边界支持TXT、ZIP和7z。归档只在受控临时目录中展开TXT，并在退出上下文时清理；任何加密、损坏、路径穿越、符号链接、重复路径或容量超限均失败关闭。`HuaHongBatchInspector.inspect_input()` 是单文件/归档的统一检查入口。

Canonical写入分两步：`SourceFileRepository.register()`先登记来源和接收记录，调用方据此创建并启动Processing Job；CP Writer要求Supplier、Program Version、Parser Profile和完整Test Item映射，Product可由源数据或人工补录提供，也可以为空。CP和FT使用独立Writer/Adapter，只在清洗后写入公共Run/Unit/Measurement模型。

人工补录接口按CP/FT分别限制字段，可对一个Import Batch或其中一个Source File记录`FILL`或`IGNORE`决定。再次填写同一字段会保留旧版本并切换当前记录；补录结果不修改Cleaner的源解析事实。

Dataset发布链要求先建立Dataset Version并显式关联Processing Run。DQ Gate会检查Run状态、输入批次血缘、重复Source、Dataset身份范围及未关闭的阻断DQ问题；只有Gate为PASS且发布用户有效时，Publisher才会在一个事务中切换当前版本和当前Processing Run。结果摘要接口返回Lot、Wafer、Die、Pass/Fail、Yield、Measurement和Bin分布。

最新版 Cleaner 导出、显式重清洗和逻辑归档是三种不同动作：导出只创建临时 Artifact，不改 Canonical；重清洗通过兼容的最新已发布 Cleaner 创建新 Dataset Version；归档退出 Current View 但保留 Source、Batch、Job、Run 和 `test.*`。三种动作均受 RBAC、Owner、理由、幂等和审计约束，TMS 不删除 FTP/NAS 原始源。

真实SQL Server集成验证脚本：

```powershell
& .\.conda-env\python.exe scripts\g0\verify_canonical_dataset_pipeline.py `
  --server <SQL服务器> --user <SQL登录名>
```

脚本通过安全密码提示读取凭据，使用带随机标识的合成G0数据验证完整链路，并在结束时按外键顺序清理及独立复核测试数据。成功输出同时包含 `canonical_dataset_pipeline=PASS` 和 `integration_cleanup=PASS`。

Analytics Export 使用现有 `delivery.export_job -> delivery.export_artifact` 链路，Worker 仅读取创建任务时固定的 Dataset Version、Filter、Parameter 和 Rule Context，不修改 `test.*` Canonical 数据。`sql2014_0021` 为 Worker 增加 attempt、lease、heartbeat 和 fencing token；过期的 `RUNNING` 可由新 Worker 使用新 token 安全接管，旧 Worker 不能登记 Artifact，达到最大尝试数后稳定失败。导出根目录必须是绝对路径，且只在 `TMS_ANALYTICS_EXPORT_ROOT/<export_job_id>` 直接子目录内按 attempt 原子生成文件：

```powershell
. .\.env.runtime.ps1
& .\.conda-env\python.exe scripts\run_analytics_export_worker.py --once
```

分析导出 TTL 清理默认只预览，不写数据库、不删文件。执行模式只处理已到期的 `SUCCESS` Job；验证所有路径都是 `TMS_ANALYTICS_EXPORT_ROOT/<export_job_id>` 的直接文件且不存在 symlink/reparse point 后，删除精确 Job 根。系统保留 Artifact 的文件名、MIME、大小、SHA 和审计元数据，把物理状态置为 `DELETED/MISSING`，Job 置为 `EXPIRED`。生产启用 Delete 前应先持续审阅 DryRun：

```powershell
& .\.conda-env\python.exe scripts\run_analytics_export_cleanup.py --limit 100
& .\.conda-env\python.exe scripts\run_analytics_export_cleanup.py --limit 100 --delete
```

正式分析按 Overview、Detail、Parameter、Spatial、Quality、Delivery 六组提供后端强制 kill switch：`TMS_ANALYTICS_OVERVIEW_ENABLED`、`TMS_ANALYTICS_DETAIL_ENABLED`、`TMS_ANALYTICS_PARAMETER_ENABLED`、`TMS_ANALYTICS_SPATIAL_ENABLED`、`TMS_ANALYTICS_QUALITY_ENABLED`、`TMS_ANALYTICS_DELIVERY_ENABLED`。默认为 `true`；关闭后直接 URL 也返回 `ANALYSIS_FEATURE_DISABLED`，前端从 `/api/v1/analytics/features` 显示具体原因。

Overview 的即时统计风险必须由用户显式调用 `POST /api/v1/analytics/instant-risk`，不会在首次进入页面时自动执行。请求按同一 Dataset/Filter Context 固定 1–6 个方法，并为 Capability、PAT、SPC、Margin、SBL、SYL 分别提交 exact `rule_code + version_code`；任何版本未完成三方批准和激活都会失败关闭。Cpk/Ppk 风险使用哪个指标及低值阈值必须包含在已批准 CPK Rule 的 `capability_risk_metric` / `capability_risk_threshold` 中，前端不提供业务默认值或自行判定；其余方法只汇总各权威服务已计算的 status、evidence 与 control/limit 结果。

新的 `ZONE_COMPARISON` 请求只接受已批准并激活的 `WAFER_ZONE_GEOMETRY_V2`；V1 仍可读取和治理，但不能为新请求提供隐含象限语义。V2 除径向 Center/Mid/Edge 几何外，必须显式版本化 `quadrant_axis_rotation_degrees`、`quadrant_y_direction` 和四个唯一的 `quadrant_labels_ccw`。服务端同时返回径向与象限聚合、批准轴参数及全部成员 Unit key；Composite 坐标同样返回与 `observed_count` 一一对账的成员 key，所有响应项都计入 `max_points` 门禁，前端不会用代表 Unit 冒充聚合总体。

当前能力矩阵为：`ANALYTICS_DETAIL` / `PARAMETER_DETAIL` 只生成 `CSV`、`XLSX`、`BIN_TXT`；`ANALYTICS_OVERVIEW`、`PARAMETER_ANALYSIS`、`PARAMETER_RELATIONSHIP`、`SPATIAL_ANALYSIS`、`FT_QUALITY`、`WAFER_SUMMARY` 只在 `REPORT` 范围生成 `CSV`、`XLSX`、`HTML`、`PDF`、`PNG`，报告模板明确拒绝 `BIN_TXT`。每个报告必须带版本化、有界的 `chart_config.analysis` 重建合同（`ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1`），其 section 必须和 template 一致；Parameter、Relationship、Spatial、FT Quality 分别调用各自的服务端分析服务，Wafer Summary 调用服务端 wafer 分页汇总，不再复用 Unit Detail 或 Overview 内容。建立 Export Job 时，服务端从该强类型合同提取所有 exact Rule，对每个 Dataset / Supplier / Product / Parameter 或 Bin scope 重新校验三方批准、`RELEASED + ENABLED`、激活范围和算法类型，再由服务端并入冻结 `rule_context`；客户端字段不是信任边界。Saved Analysis 对 `ANALYSIS_VIEW_STATE_V1` 执行同样的 exact Rule 提取和冻结，create/revision 失配时失败关闭，restore 遇批准撤销、禁用或 scope 变化时标记 `RULE_CHANGED`。每行标准化结果携带 record type 和可追溯细节，制品 Context 同时保留 Filter/Context/Presentation Hash。PDF/PNG 使用 `requirements.txt` 中声明的 ReportLab/Pillow，完整消费同一份结果行并记录总行数，制品中只展示有界 Context 和前 50 行预览。优先选择系统 Unicode 字体，字体不可用时使用确定性 ASCII `\uXXXX` 回退；依赖缺失则稳定记录为 `FAILED / ANALYTICS_EXPORT_DEPENDENCY_UNAVAILABLE`。下载前会重新检查 Owner/System Admin 权限、Job 终态、TTL、文件路径、大小和 SHA256，API 不返回 `storage_uri`。可用开发库做只读内容冒烟：

```powershell
& .\.conda-env\python.exe scripts\g0\smoke_analytics_export_content.py `
  --output-root "$env:TEMP\tms-analytics-export-smoke" --test-stage CP

& .\.conda-env\python.exe scripts\g0\smoke_analytics_export_content.py `
  --output-root "$env:TEMP\tms-analytics-export-pdf-smoke" `
  --template ANALYTICS_OVERVIEW --format PDF
```

## 测试

```powershell
$env:PYTHONPATH = "$PWD\backend"
& .\.conda-env\python.exe -m pytest -q tests
```

前端、Migration、真实样本、浏览器和发布包的最终回归结果统一记录在 `docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md`。本地测试不能替代目标 SQL Server SP3、正式服务账号 ACL、HTTPS、备份恢复和业务 UAT。

真实数据库与现有华虹 Cleaner 的 Route A Worker 验证：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\python.exe scripts\g0\verify_route_a_worker_foundation.py
```
