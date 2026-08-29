# TMS 本机测试使用指南

## 1. 适用范围

本指南用于在当前 Windows 开发机上测试 CP/FT 正式数据清洗、缺 Lot 补录、数据库入库、Dataset Current、质量视图和 A5 生命周期。测试环境只允许本机访问，不用于局域网或生产发布。

## 2. 启动与检查

1. 双击仓库根目录的 `启动TMS测试环境.bat`。
2. 等待窗口显示 `TMS local test environment is ready`，浏览器会打开 `http://127.0.0.1:5173/`。
3. 双击 `查看TMS测试环境状态.bat`。`all_ready` 以及以下三项都应为 `True`：
   - `api_ready`
   - `worker_ready`
   - `frontend_ready`

同时确认 `database` 与 `worker_database` 都是 `TMS_G0_DEV`，`schema_revision` 与 `worker_schema_revision` 都是当前仓库唯一 head `sql2014_0018`，数据库服务器身份也一致。只要其中一项不一致，就不要提交测试数据。

默认入口关闭登录验证并使用开发管理员身份，便于先验收功能。它仍连接 `.env.runtime.ps1` 指定的 SQL 开发库，上传任务会由常驻 Worker 自动处理。角色和越权验收必须使用 `start_tms_local_test.ps1 -UseConfiguredAuthentication`，不能用免登录结果代替。

## 3. 正常测试步骤

1. 从左侧进入四个固定入口之一：工程数据/CP、工程数据/FT、量产数据/CP、量产数据/FT。
2. 点击“上传数据”。
3. 明确核对晶圆厂或封测厂；厂家选错时系统会严格失败，不能靠“重新处理”改变厂家，应重新上传并选择正确厂家。
4. 二选一提供输入：
   - 浏览器上传允许格式的源文件；或
   - 从管理员登记的 Source Catalog 选择 `root_code` 和相对目录，先查看 Manifest 文件数、总字节和指纹，再提交同一指纹。
   普通用户不能填写任意服务器绝对路径；目录在预览后发生变化时必须重新预览。
5. 提交后观察状态自动经过“排队中/处理中”，最终进入以下一种状态：
   - “已处理”：进入“清洗结果”，点击“数据分析”；
   - “待补录”：点击“补录批次号”；
   - “失败”：打开“失败详情”，记录 Batch 和 Job 后排查格式或厂家。
6. 清洗完成后无需手动点刷新，“清洗结果”会再执行一次终态刷新。
7. 进入“Dataset Current”可按产品、Lot、厂家、工程/量产、CP/FT、状态和时间检索；点击 Dataset 或 Job 可通过 URL 深链返回同一筛选和详情。

## 4. 缺 Lot 怎么处理

1. 在“原始文件”页找到“待补录”批次，点击“补录批次号”。
2. 按文件填写已核实的 Lot。若多个文件确属同一 Lot，必须显式确认并填写依据。
3. 保存后系统创建同一 Cleaner Release 的子 Job，自动重新校验、清洗和入库。
4. Lot 填错后不要继续使用该批次；当前版本不支持修改已提交决定，应重新上传源文件并填写正确 Lot。

## 5. 图表验收

在“清洗结果”点击“数据分析”：

- CP 应显示 Yield、Bin、参数和 Wafer Map；
- FT 应显示产品、源文件 Run、参数数量、器件散点、测试条件及规格线；
- 源数据没有 PASS/FAIL 或 Bin 时，FT 良率保持空值，页面会明确说明，不应猜测良率。

## 6. 导出、重清洗和归档

三种动作不能混用：

1. **最新版 Cleaner 导出**：创建独立 Export Job 和有 TTL 的临时 Artifact；只用于下载，不改变 Dataset Version、Current 或 Canonical。
2. **显式重清洗**：必须填写理由；系统选择同一 Format Profile 合同下兼容的最新已发布 Cleaner，创建新 Job，成功后产生新 Dataset Version。不要把“导出”当作更新数据。
3. **逻辑归档**：只对有权 Owner/管理员开放，必须二次确认和填写理由；归档后 Dataset 退出 Current 目录，但 Source、Batch、Job、历史 Version 和 `test.*` 不删除，FTP/NAS 原始文件也不会由 TMS 删除。

执行后打开 Job 详情，核对 Parent Job、Cleaner Release、Lifecycle Action、状态时间线和全部来源 SHA。导出 Artifact 过期后不应继续提供下载链接。

## 7. 质量、主数据和运维页面

- 有 `MANAGEMENT_READ` 的管理/质量角色可按时间、产品、Lot、厂家、阶段查看产量、已知良率、未知占比、异常任务和数据新鲜度；良率分母不包含 UNKNOWN Unit。
- 产品 crosswalk 默认是 PENDING。读取权限不等于批准权限；只有治理角色在核实 SAP-B1 物料键和依据后才能批准或拒绝。
- 有 `AUDIT_READ` 的运维角色可查看 Environment、Database、Server、Schema、一致性和 Worker 心跳；只有系统管理员可以 Drain/Resume Worker。
- 没有权限的菜单应隐藏，直接访问路由或 API 仍应返回 Unauthorized/Forbidden。

## 8. 停止

测试结束后双击 `停止TMS测试环境.bat`。入口按以下顺序收尾：

1. 关闭前端，阻止新任务；
2. Worker 检测停止请求后，在当前安全执行单元结束时退出，不强制中断 Cleaner；
3. 停止 API。

如果当前清洗超过 60 秒仍未完成，停止脚本不会强杀 Worker，会保留 API 和运行状态。等待任务完成后再次双击停止即可。停止瞬间已经进入领取事务的任务仍会安全做完，因此应以页面不再显示“处理中”为最终停止条件。

## 9. 报障时提供的信息

至少记录：

- 工程/量产、CP/FT；
- 厂家；
- Batch 编号；
- Job 编号；
- 页面状态和错误类型；
- Source Catalog 代码、相对目录和 Manifest 指纹（不要发送绝对存储 URI）；
- 是否缺 Lot、补录值及确认依据；
- 是否执行导出、重清洗或归档，以及 Parent Job/Lifecycle Action；
- `查看TMS测试环境状态.bat` 的结果。

不要发送数据库密码、运行配置、原始客户数据或日志中的敏感内容。

## 10. 当前边界

- 本入口只服务本机 `127.0.0.1`；其他电脑测试需要正式前端部署、反向代理、认证、HTTPS和防火墙方案。
- 默认免登录仅用于功能冒烟。真实账号/Owner/角色 UAT 使用 `start_tms_local_test.ps1 -UseConfiguredAuthentication`，并提前完成账号启用和 CP/FT 角色分配。
- 正式目录提交已经使用 Source Catalog + 相对路径 + Manifest 指纹；浏览器和普通用户不得提交任意服务器绝对路径。
- 生产计划任务、服务账号 ACL、Windows Server 重启恢复属于单独的生产发布验收。
- 当前工作区运行配置按单用户开发机管理；不得把本机免登录入口复制到共享电脑或生产服务器使用。
- 当前只完成仓库和开发库 G0-G2；G3 测试服务器、G4 生产分批、SP3、HTTPS、正式备份恢复和业务签字未执行。
