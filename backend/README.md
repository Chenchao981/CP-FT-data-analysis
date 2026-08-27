# TMS Backend

> 正式数据执行主线为 Route A；一次性PAT使用隔离的Quick Analysis Workspace。两条通道共享SQL队列和Worker，但只有正式导入写入Canonical。

## 开发环境

```powershell
conda create -p .conda-env python=3.12 alembic sqlalchemy pyodbc fastapi uvicorn pytest httpx
$env:PYTHONPATH = "$PWD\backend"
```

数据库连接通过进程环境变量 `TMS_DATABASE_URL` 注入，不写入仓库。

## 启动

```powershell
$env:PYTHONPATH = "$PWD\backend"
& .\.conda-env\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开一个 PowerShell 窗口启动 Route A Worker：

```powershell
. .\.env.runtime.ps1
$env:PYTHONPATH = "$PWD\backend"
$env:TMS_JOB_REPOSITORY = "sql"
& .\.conda-env\python.exe scripts\run_route_a_worker.py
```

部署新环境时，先升级 Migration，再按实际发布包 SHA256 幂等登记 Cleaner：

```powershell
. .\.env.runtime.ps1
& .\.conda-env\Scripts\alembic.exe -c db\alembic\alembic.ini upgrade head
& .\.conda-env\python.exe scripts\g0\bootstrap_existing_cleaner_releases.py
```

接口：

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `POST /api/v1/contracts/format-profiles/validate`
- `POST /api/v1/contracts/cleaner-releases/validate`
- `GET /api/v1/contracts/cleaner-adapters`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/transitions`
- `POST /api/v1/cleaners/huahong/inspect`
- `POST /api/v1/datasets`
- `POST /api/v1/datasets/{dataset_id}/versions`
- `GET /api/v1/datasets/{dataset_id}/versions/{version_no}/gate`
- `POST /api/v1/datasets/{dataset_id}/versions/{version_no}/publish`
- `GET /api/v1/datasets/{dataset_id}/versions/{version_no}/summary`
- `GET /api/v1/datasets/{dataset_id}/versions/{version_no}/charts`
- `POST /api/v1/enrichments`
- `GET /api/v1/enrichments/batches/{import_batch_id}`
- `GET /api/v1/enrichments/fields/{CP|FT}`
- `GET /api/v1/quick-analysis/source-roots`
- `GET /api/v1/quick-analysis/source-roots/{root_code}/directories`
- `POST /api/v1/quick-analysis/pat`
- `GET /api/v1/quick-analysis/sessions`
- `GET /api/v1/quick-analysis/sessions/{analysis_session_id}`
- `GET /api/v1/quick-analysis/sessions/{analysis_session_id}/download`
- `/api/docs`

Job Service默认使用进程内实现；Route A 联调和部署必须设置 `TMS_JOB_REPOSITORY=sql`。SQL Repository支持原子领取、租约、心跳、超时恢复、幂等键和最大重试次数。

Quick Analysis通过`TMS_SOURCE_ROOTS_JSON`配置管理员受控根目录。浏览器和API只使用`source_root_code + relative_path`，不接受任意绝对路径。P0仅支持杰群统一CSV目录PAT；结果写入`TMS_QUICK_WORK_ROOT`并按`TMS_QUICK_RESULT_TTL_HOURS`登记过期时间。

Quick Analysis容量按“活跃任务预留 + 失败任务未清理预留 + 尚未清理Artifact”计算。默认按源文件总字节数的50%加64 MiB预留临时空间，并同时检查全局容量、单用户容量和工作盘最小剩余空间。部署时可通过`TMS_QUICK_GLOBAL_CAPACITY_BYTES`、`TMS_QUICK_USER_CAPACITY_BYTES`、`TMS_QUICK_MIN_FREE_BYTES`、`TMS_QUICK_RESERVE_RATIO`和`TMS_QUICK_RESERVE_OVERHEAD_BYTES`调整。

过期结果清理先用只读模式确认范围，再执行物理删除：

```powershell
. .\.env.runtime.ps1
& .\.conda-env\python.exe scripts\run_quick_artifact_cleanup.py --dry-run
& .\.conda-env\python.exe scripts\run_quick_artifact_cleanup.py
```

清理器只允许删除`TMS_QUICK_WORK_ROOT/<job_id>`这个精确子目录，遇到目录逃逸或符号链接会标记`BLOCKED`。Session、Job、Manifest、SHA和`governance.audit_log`记录不会删除。进程中断后停留在`CLEANING`的任务默认30分钟后允许恢复，可用`TMS_QUICK_CLEANUP_STALE_MINUTES`调整。

开发库可用小型合成过期文件复验完整清理链；脚本完成后会移除全部合成记录：

```powershell
. .\.env.runtime.ps1
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
. .\.env.runtime.ps1
& .\.conda-env\python.exe scripts\g0\verify_quick_pat_sql_e2e.py `
  --source-root 'JIEQUN_FT_SHARED' `
  --relative-path '520data'
```

脚本要求开发库存在一个系统管理员和一个具备`ANALYSIS_RUN`的普通用户。它保留成功的Quick Analysis会话、任务与临时Artifact作为验收记录，但不会向`test.test_run`、`test.unit_result`或`test.measurement`写入数据。

华虹文件边界支持TXT、ZIP和7z。归档只在受控临时目录中展开TXT，并在退出上下文时清理；任何加密、损坏、路径穿越、符号链接、重复路径或容量超限均失败关闭。`HuaHongBatchInspector.inspect_input()` 是单文件/归档的统一检查入口。

Canonical写入分两步：`SourceFileRepository.register()`先登记来源和接收记录，调用方据此创建并启动Processing Job；CP Writer要求Supplier、Program Version、Parser Profile和完整Test Item映射，Product可由源数据或人工补录提供，也可以为空。CP和FT使用独立Writer/Adapter，只在清洗后写入公共Run/Unit/Measurement模型。

人工补录接口按CP/FT分别限制字段，可对一个Import Batch或其中一个Source File记录`FILL`或`IGNORE`决定。再次填写同一字段会保留旧版本并切换当前记录；补录结果不修改Cleaner的源解析事实。

Dataset发布链要求先建立Dataset Version并显式关联Processing Run。DQ Gate会检查Run状态、输入批次血缘、重复Source、Dataset身份范围及未关闭的阻断DQ问题；只有Gate为PASS且发布用户有效时，Publisher才会在一个事务中切换当前版本和当前Processing Run。结果摘要接口返回Lot、Wafer、Die、Pass/Fail、Yield、Measurement和Bin分布。

真实SQL Server集成验证脚本：

```powershell
& .\.conda-env\python.exe scripts\g0\verify_canonical_dataset_pipeline.py `
  --server <SQL服务器> --user <SQL登录名>
```

脚本通过安全密码提示读取凭据，使用带随机标识的合成G0数据验证完整链路，并在结束时按外键顺序清理及独立复核测试数据。成功输出同时包含 `canonical_dataset_pipeline=PASS` 和 `integration_cleanup=PASS`。

## 测试

```powershell
$env:PYTHONPATH = "$PWD\backend"
& .\.conda-env\python.exe -m pytest -q tests
```

真实数据库与现有华虹 Cleaner 的 Route A Worker 验证：

```powershell
. .\.env.runtime.ps1
& .\.conda-env\python.exe scripts\g0\verify_route_a_worker_foundation.py
```
