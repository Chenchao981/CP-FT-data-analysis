# TMS 本机可用测试环境完成报告（2026-08-28）

## 1. 结论

当前开发机已具备可由业务人员直接使用的本机测试入口：双击一次即可启动 SQL API、Route A Worker 和 React 前端。真实日月新 FT 样本已从浏览器提交，两个批次均无需人工执行 Worker 命令即完成清洗、Canonical 入库、结果展示和图表加载。

本结论限定于本机免登录功能验收，不等同于 Windows Server 生产发布或普通账号权限 UAT。

## 2. 本次完成内容

### 2.1 一键运行环境

- 新增根目录启动、状态、停止三个双击入口。
- 后台直接管理 Python/Uvicorn、Python/Route A Worker 和 Node/Vite 三个真实进程。
- API 和前端固定监听 `127.0.0.1:8000/5173`，Vite 使用 `strictPort`，不静默换端口。
- 启动前验证 Python、Node、Vite、运行配置、`TMS_JOB_REPOSITORY=sql` 和入口文件；先从连接配置解析数据库名并拒绝非 `TMS_G0_DEV`，再由 API ready 硬校验数据库与 `sql2014_0014` Schema，通过后才允许启动 Worker。
- 只复用同一状态文件记录且 PID/创建时间/代码内固定的可执行文件和命令标识全部匹配的进程；外部占用端口或外部 Worker 会失败关闭。
- 状态文件和启动日志写入 Git 忽略的 `artifacts/runtime/local-test`，目录 ACL 只保留当前用户、SYSTEM 和本机管理员。
- start/stop 使用同一命名互斥锁，并在启动每个角色前写入 `STARTING + pending_role`；新进程取得 PID 后立即加入回滚清单并原子写入状态，重复启动时按角色合并记录，不会覆盖尚未处理的既有进程。
- Worker 最后启动；它在创建 Queue、写 ready 或领取任务之前，自行 fail-closed 校验期望的数据库名、Schema 和 SQL Server 身份。启动器与状态页也再次要求 API 和 Worker 指向同一个 `TMS_G0_DEV / sql2014_0014 / SQL Server`，并核对监听 PID、Worker ready 和 drain 状态。
- Vite 和浏览器启动前临时清除全部 `TMS_*` 与 `PYTHONPATH`，后端数据库和 JWT 配置不会继承到前端进程。
- 默认免认证便于本机功能验收；提供 `-UseConfiguredAuthentication` 真实认证模式。

### 2.2 安全停止

- 先关闭前端，阻止继续提交新任务。
- 本机 Worker 支持 `--stop-file` 优雅停止合同：在当前安全执行单元结束时退出，不直接中断 Cleaner。
- 默认等待 60 秒；超时不强杀 Worker，也不停止 API，用户可在任务完成后再次停止。
- Worker 安全退出后再停止 API，并清理本次运行状态和 stop 文件。

### 2.3 Windows PowerShell 5.1 UTF-8 修复

实跑时发现 Windows PowerShell 5.1 会按系统编码读取无 BOM 的 `.env.runtime.ps1`，导致 `TMS_SOURCE_ROOTS_JSON` 中的中文路径损坏，Worker 报 `TMS_SOURCE_ROOTS_JSON is not valid JSON` 后退出。

现在所有 Windows 运行入口统一：

- 以严格 UTF-8 读取配置；
- 对配置执行 PowerShell 语法校验；
- 拒绝非法 UTF-8；
- 不把配置值或秘密写入命令行、状态文件和启动输出。

修复同时覆盖本机入口、生产 API/Worker 包装脚本和旧的开发 API 启动脚本。

### 2.4 前端终态刷新

首次真实批次暴露出一个 UI 竞态：上传状态从“处理中”变成“已处理”时，结果轮询可能先停止，导致“清洗结果”必须手动刷新。

前端现在记录活动 Batch 集合；任一活动批次进入 `PROCESSED/NEEDS_INPUT/FAILED` 终态后，结果查询再失效并刷新一次。第二个真实批次证明新结果无需点击“刷新”即可出现。

### 2.5 UTC 时间显示

SQL Server 返回的 UTC 字段有时不带时区后缀，浏览器原先会把它误当作本地时间，页面少显示 8 小时。CP/FT 和快速分析现在统一把无偏移量时间按 UTC 解析，再按 `Asia/Shanghai` 显示。浏览器复验 Batch 54 的上传/完成时间为 `2026/8/28 08:10:30` 和 `08:10:47`。

## 3. 真实浏览器与数据库证据

两个输入都是原日月新 DC XLSX 的只读副本，原文件与副本 SHA256 完全一致。

| Batch | Job | Dataset | Lot | 文件 | 单元数 | 参数数 | Job结果 | Job耗时 |
|---:|---:|---:|---|---|---:|---:|---|---:|
| 53 | 70 | 24 v1 | FA53-4115 | `NCT5516020...xlsx` | 4,962 | 18 | SUCCESS | 13.233 秒 |
| 54 | 71 | 25 v1 | FA53-4115 | `NCT5516019...xlsx` | 6,334 | 18 | SUCCESS | 15.529 秒 |

SHA256：

- `NCT5516020...xlsx`：`C0894974020EB652815051FADCF01D3757DFC60FC25542B157E85A6D95D74529`
- `NCT5516019...xlsx`：`F105C65575E9F19474B5810B7B42F7A2A1F78F84F74F1C2B99652143090030E4`

浏览器验收结果：

1. 量产数据/FT 页面提交服务器受控目录；
2. Batch 54 自动经过队列并显示“已处理”；
3. “清洗结果”自动出现 6,334 单元、18 参数，无需手动刷新；
4. 直达 Dataset 25 v1；
5. FT 分析页显示产品、1 个源文件 Run、18 个参数、6,334 个当前参数测量点；
6. `VTH1(V)` 显示 LSL 1.25 V、USL 2.2 V、测试条件 `ID=250uA`，散点图和规格线视觉正常；
7. 因源数据不提供可发布 PASS/FAIL 或 Bin，良率保持空值并由页面明确说明。

## 4. 自动验证

| 验证 | 结果 |
|---|---|
| 后端全量测试 | 206 passed，4 个 openpyxl 弃用警告 |
| 前端合同测试 | 13 files / 34 tests passed |
| 前端生产构建 | PASS，13,044 modules transformed |
| 本地运行专项测试 | 11 passed：UTF-8、非 DEV 数据库拒绝、前端秘密隔离、状态按角色合并、Worker stop/ready、Worker 数据库身份 fail-closed 与健康合同 |
| API/Worker/本地启动 `ValidateOnly` | 全部 VALID |
| 完整停止后重新启动 | PASS，三进程均为新 PID |
| 空闲 Worker drain | PASS，Worker 优雅退出后才停止 API |
| 外部进程占用 5173 | PASS，启动在创建任何 TMS 进程和状态文件前拒绝，外部进程保持运行 |
| 重复启动 | PASS，三进程均被安全收养且记录保持完整 |
| Worker 错误 SQL Server 指纹 | PASS，在创建 Queue/领取 Job 前直接退出 |
| 最终状态 | `all_ready/api_ready/worker_ready/frontend_ready = true`；API 与 Worker 数据库身份一致 |

前端构建仍提示两个大 Chunk 超过 500 kB；不阻断本机功能测试，但正式部署前应继续按页面拆包。

## 5. 做得好的地方

- 不是只验证端口，而是用真实 FT 数据打通浏览器、SQL Queue、Worker、Canonical、结果和 ECharts。
- 第一批实际暴露的 UTF-8 与结果漏刷问题都在同一里程碑修复，并由第二批复验。
- 原始源文件未修改，测试副本在 Git 忽略目录，SHA 可追溯。
- 本机测试入口与生产计划任务保持分离，未用开发便利逻辑替代生产部署合同。
- 停止流程具备 drain，降低用户误操作中断 Cleaner 的风险。

## 6. 不确定性与不足

1. 本轮浏览器证据是免登录开发管理员，不是 CP_ENGINEER/FT_ENGINEER 普通账号的 Owner 与权限 UAT。
2. 本轮新增实测为日月新 FT；CP、日月光及缺 Lot 闭环沿用此前通过的能力，本报告不把它们重复计为本轮新证据。
3. 当前只允许本机访问；局域网测试尚无前端静态部署、反向代理、HTTPS和防火墙方案。
4. 正式上传的绝对 `source_path` 仍是受控开发能力；广泛用户和生产前需收口为 Source Catalog/Storage Adapter。
5. 当前工作区和 `.env.runtime.ps1` 继承开发机权限；本机入口只适合当前单用户开发机。共享电脑或生产服务账号使用前必须收紧工作区、release、脚本和运行配置 ACL，当前权限不能作为生产安全基线。
6. Worker 目前依靠启动器状态校验避免本机重复实例，尚无跨启动方式的全局单实例锁和可查询心跳接口。
7. stop 文件与 SQL claim 之间存在极小边界：停止瞬间已经进入领取事务的一个任务仍会完整执行；脚本不会强杀它，用户应等待页面无“处理中”后再次停止。若生产合同要求停止后绝不再 claim，需增加数据库级 drain gate。

## 7. 下一步

用户现在可以按《TMS 本机测试使用指南》直接开始功能测试。建议按顺序：

1. 先测已知 Lot 的日月新/日月光 FT；
2. 再测缺 Lot 的补录闭环；
3. 再测华虹/Jetech/立昂微 CP；
4. 记录 Batch/Job 和界面问题；
5. 功能口径稳定后，再启动真实认证普通账号 UAT与 Windows Server 生产发布门禁。
