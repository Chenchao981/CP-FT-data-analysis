# TMS Frontend

## 本地运行

先启动后端（默认内存任务仓库）：

```powershell
$env:PYTHONPATH = "backend"
.\.conda-env\python.exe -m uvicorn app.main:app --reload
```

再启动前端：

```powershell
cd frontend
npm install
npm run dev
```

Vite会将 `/api` 转发到 `http://127.0.0.1:8000`。生产构建使用 `npm run build`，合同测试使用 `npm test`。

当前页面实现清洗任务创建、读取及 `QUEUED → RUNNING → SUCCESS`/取消状态流转，以及华虹单片TXT格式、身份、Schema、Die、Bin和良率检查。结果审核、图表分析和规则治理菜单暂未开放。
