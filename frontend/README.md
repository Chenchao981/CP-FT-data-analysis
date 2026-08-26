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

当前页面包含工程/量产CP与FT正式数据、快速分析、分析图表以及用户权限。快速分析P0可在管理员配置的数据源中浏览相对目录，提交杰群FT PAT后台任务、查看状态并下载带TTL的结果；原始文件不经浏览器上传。
