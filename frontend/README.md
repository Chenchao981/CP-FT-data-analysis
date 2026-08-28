# TMS Frontend

## 本地运行

从仓库根目录进行完整 SQL Queue 联调时，直接双击：

```powershell
.\启动TMS测试环境.bat
```

这个入口会同时启动 API、Route A Worker 和 Vite。只启动 API 与 Vite 会导致上传任务留在队列中，不能作为正式清洗闭环验收。状态和停止入口分别是：

```powershell
.\查看TMS测试环境状态.bat
.\停止TMS测试环境.bat
```

前端单独开发组件时，才在 `frontend` 目录执行 `npm run dev`。Vite会将 `/api` 转发到 `http://127.0.0.1:8000`。生产构建使用 `npm run build`，合同测试使用 `npm test`。

本机一键入口只监听 `127.0.0.1`，默认使用免登录开发管理员。需要验证真实登录和角色权限时，先停止当前环境，再在仓库根目录执行：

```powershell
.\scripts\windows\start_tms_local_test.ps1 -UseConfiguredAuthentication
```

此模式遵循 `.env.runtime.ps1` 中的认证设置；普通账号必须先完成注册、管理员启用和 CP/FT 角色分配。

当前页面包含工程/量产CP与FT正式数据、快速分析、分析图表以及用户权限。快速分析P0可在管理员配置的数据源中浏览相对目录，提交杰群FT PAT后台任务、查看状态并下载带TTL的结果；原始文件不经浏览器上传。
