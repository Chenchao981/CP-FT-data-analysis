# NCE PYMS Frontend

当前用户入口为 CP 数据、FT 数据、个人分析工具、产品与批次、良率与质量及管理页面。旧工程/量产链接转到 CP/FT 两入口；正式数据和个人工具结果分开。

## 本地开发与验收

在项目根目录启动完整测试环境：

```powershell
.\scripts\windows\start_tms_local_test.ps1 -UseConfiguredAuthentication
.\scripts\windows\get_tms_local_test_status.ps1 -AsJson -RequireReady
```

它加载本地 SQL 配置，管理四个进程：API、正式/个人清洗 Worker、分析报告 Worker、Vite 页面。四项均就绪且数据库、服务器、Schema 一致才返回 all_ready。遗漏报告 Worker 时，上传可能正常而报告会一直排队。

网页地址为 http://127.0.0.1:5173，登录使用现有已启用账号。保留配置认证的验收应使用上述参数；根目录批处理默认使用回环地址开发免登录模式，不应将其结果算作登录验收。

停止入口会先让两个 Worker 完成当前任务，再停止 API，避免中断写入或生成中的报告：

```powershell
.\scripts\windows\stop_tms_local_test.ps1
```

修改后端后需重启；Vite 会刷新前端代码。单独开发组件时，在 frontend 执行 npm run dev；该命令不会启动数据库任务处理程序。

## 检查

在 frontend 目录执行：

```powershell
npm test
npm run build
```

测试按文件串行，避免 Ant Design/jsdom 资源竞争；全部测试完成才算全量通过。生产构建包括 TypeScript 检查。当前首屏分块仍较大，构建成功不代表多用户或首屏性能验收。

[当前复审执行单](../docs/development/NCE_PYMS_Project_Review_and_Closure_Plan_2026-09-05.md)记录需求边界和本次收口范围。目标服务器、第二台员工电脑和业务 UAT 另行验收。
