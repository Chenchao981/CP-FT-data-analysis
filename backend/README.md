# TMS Backend

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
- `/api/docs`

Job Service默认使用进程内实现；设置 `TMS_JOB_REPOSITORY=sql` 后使用SQL Repository，已在企业版隔离库完成状态流转集成验证。华虹接口上传单片TXT后返回身份、Schema、Die、Bin和良率摘要，未知Schema或身份不一致返回422。

华虹文件边界支持TXT、ZIP和7z。归档只在受控临时目录中展开TXT，并在退出上下文时清理；任何加密、损坏、路径穿越、符号链接、重复路径或容量超限均失败关闭。`HuaHongBatchInspector.inspect_input()` 是单文件/归档的统一检查入口。

Canonical写入分两步：`SourceFileRepository.register()`先登记来源和接收记录，调用方据此创建并启动Processing Job；CP Writer要求Supplier、Program Version、Parser Profile和完整Test Item映射，Product可由源数据或人工补录提供，也可以为空。CP和FT使用独立Writer/Adapter，只在清洗后写入公共Run/Unit/Measurement模型。

人工补录接口按CP/FT分别限制字段，可对一个Import Batch或其中一个Source File记录`FILL`或`IGNORE`决定。再次填写同一字段会保留旧版本并切换当前记录；补录结果不修改Cleaner的源解析事实。

Dataset发布链要求先建立Dataset Version并显式关联Processing Run。DQ Gate会检查Run状态、输入批次血缘、重复Source、Dataset身份范围及未关闭的阻断DQ问题；只有Gate为PASS且发布用户有效时，Publisher才会在一个事务中切换当前版本和当前Processing Run。结果摘要接口返回Lot、Wafer、Die、Pass/Fail、Yield、Measurement和Bin分布。

真实SQL Server集成验证脚本：

```powershell
& .\.conda-env\python.exe scripts\g0\verify_canonical_dataset_pipeline.py `
  --server 192.168.18.132 --user <SQL登录名>
```

脚本通过安全密码提示读取凭据，使用带随机标识的合成G0数据验证完整链路，并在结束时按外键顺序清理及独立复核测试数据。成功输出同时包含 `canonical_dataset_pipeline=PASS` 和 `integration_cleanup=PASS`。

## 测试

```powershell
$env:PYTHONPATH = "$PWD\backend"
& .\.conda-env\python.exe -m pytest -q tests
```
