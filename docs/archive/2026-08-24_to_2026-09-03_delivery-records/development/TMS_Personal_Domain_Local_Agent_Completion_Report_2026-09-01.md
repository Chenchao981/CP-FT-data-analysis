# TMS PERSONAL / DOMAIN 与 Local Agent 完成报告

- 日期：2026-09-01
- 状态：开发候选 PASS；不等同于 G3 测试服务器批准或 G4 生产上线
- 需求基线：`docs/business/TMS_Data_Ownership_and_Local_Analysis_Requirements_v1.0_2026-09-01.md`
- 开发计划：`docs/development/TMS_Personal_Domain_Local_Agent_Development_Plan_v1.0_2026-09-01.md`

## 1. 做了什么

1. 将数据内容权限收敛为两类：用户手工上传和本机快速分析为 `PERSONAL`，只允许
   Owner；FTP/NAS/API/SAP 等受控系统源为 `DOMAIN`，只允许有效且未过期的数据域授权
   用户。
2. 新增 Data Domain、Grant、Source Definition 和 `access_scope/data_domain_id` 数据模型，
   并把统一可见性谓词应用于 Dataset、分析、Job、Saved Analysis、导出和 Artifact 等
   主要数据出口。`business_domain=ENGINEERING/PRODUCTION` 只保留业务分类，不再作为 ACL。
3. `SERVER_CATALOG/QUICK_ANALYSIS` 与 `FORMAL_IMPORT` Source Root 都要求显式绑定
   `data_domain_code`；Root 列举、浏览、预览和任务创建按当前有效授权过滤。
4. 重构 Quick Analysis 为双位置执行：服务器受控目录由 near-data Worker 处理；用户电脑
   目录由回环 Local Agent 原生选择并调用已发布工具。Local Agent 不接收 TMS JWT，也不
   上传原始 CP/FT 文件。
5. 接通杰群 FT 原始目录 PAT，直接复用
   `factories.jiequn.pat_cleaner.generate_raw_pat`，执行前校验发布包 SHA，不在 TMS/Agent
   重写解析、单位换算、参数选择或 PAT 公式。CP 原始目录 PAT 保持明确禁用门。
6. Local Agent 配对令牌固定有效 8 小时；失败运行、结果登记确认和 Agent 重启后的陈旧
   安全 UUID 目录具有有界清理。Agent 重启不恢复中断任务，需重新选择和运行。
7. TMS Local Result 接收端增加 HTTP 总大小限制、流式大小/SHA 对账、严格 XLSX 容器
   白名单、唯一 `PAT` 工作表、固定 17 列、数值与摘要计数校验，再执行原子登记。
8. `SERVER_CATALOG` Quick 会话和结果继承 Source Root 的数据域；`LOCAL_AGENT` 会话固定为
   PERSONAL。Quick 列表、详情、Job、下载、Worker 开始和成功登记均复核当前授权，撤权、
   过期、域停用或发起账号停用后失败关闭。
9. 最终权限审计进一步关闭两类派生数据旁路：Dataset Version 只能把同一 PERSONAL Owner、
   同一 Batch 血缘和相同业务身份的已完成 Run 关联起来；Saved Analysis 和两套异步导出在
   读取敏感上下文及登记成功 Artifact 前复核权限。撤权后不登记 `SUCCESS`、不登记
   Artifact、也不允许交付；当前 attempt 的物理输出执行 best-effort 清理，清理失败必须
   进入 orphan retry，不能宣称物理文件绝对即时删除。
10. `DATA_BREAK_GLASS` 只保留未启用的代码扩展点；Migration 和只读验证器对任意角色绑定
    都 fail-closed，开发账号也不含该权限。Worker 启停统一要求 `SYSTEM_OPERATE`，不再按
    角色名称放行。
11. 历史 Dataset 迁移按全部 Dataset Version → Batch 血缘判定：只有所有血缘均为同一
    PERSONAL Owner 才迁为 PERSONAL，混合、缺失或不确定血缘进入迁移 Hold；Published
    Cleaner Release 按发布快照不可变处理，登记内容不一致时必须新建版本。
12. 中央开发候选 Release 已完整包含 Local Agent Python 模块、README、示例配置和 Windows
    启动脚本；Release discovery 明确排除 Agent 测试、缓存、日志、工作目录、结果、真实配置
    和令牌，并在解包后实际运行 `python -m local_agent ... --validate-only` 验证入口。

## 2. 已确定的结论

- “个人上传/个人本机分析，个人查看”是合理且已实现的默认边界；系统管理员、用户管理员
  和数据域管理员不会因管理身份自动获得数据内容权限。
- 数据权来自 Owner 或 Source Definition 绑定的数据域，不来自任务执行人、待办接收人、
  部门、角色名称或 `ENGINEERING/PRODUCTION` 标签。
- 创建任务、关联 Run、保存分析或排队导出都不能获得或扩大数据权；所有派生结果必须继承
  输入数据的 PERSONAL/DOMAIN 边界。DOMAIN 撤权后，下一次读取和最终成功登记立即失败；
  若撤权发生在 Worker 最后一次执行前复核之后，受信 Worker 可能继续本次计算，但原子
  final ACL 会阻止 `SUCCESS`、Artifact 登记和结果交付。
- 本机 520 个杰群 CSV 不需要上传到中央服务器；Local Agent 在数据所在电脑执行，TMS
  只接收 PAT 结果、摘要和 Manifest，正式 `test.*` Canonical 不因 Quick 分析增加记录。
- Local Agent 回执是“自声明一致性回执”，只用于 Release、Manifest、摘要和结果之间的
  合同对账，不是设备签名或可信执行证明。

## 3. 已完成验证

| 验证项 | 结果 | 证据/说明 |
|---|---|---|
| 开发库 Migration | PASS | SQL Server schema head 从 `sql2014_0023` 增量升级到 `sql2014_0024` |
| 随机空库 Migration | PASS | 从 `sql2014_0001` 连续升级到 `sql2014_0024` |
| Migration/ACL 只读复核 | PASS | 绑定完整性 8 项均为 0；36 个 PERSONAL Batch、31 个 PERSONAL Dataset、1 个 PERSONAL Quick、2 个 DOMAIN Quick 均符合约束 |
| Local Agent 单元/API 负向测试 | PASS | 31 passed；覆盖 Origin/Host/token、令牌过期、Manifest 漂移、SHA 错误、运行删除等 |
| Local Result 接收与正文限制 | PASS | 严格回执、大小/SHA、XLSX 合同与超限失败关闭的定向测试通过 |
| Dataset/Batch/Run 血缘与权限 | PASS | Dataset API/SQL 定向回归 135 passed；跨 Owner、跨 Batch、失败 Run、未完成 Job、DOMAIN 手工拼接均拒绝 |
| Saved Analysis 与异步导出撤权 | PASS | 定向回归 155 passed、2 skipped；敏感 JSON 读取前拒绝，撤权后无 `SUCCESS`/Artifact/交付；2 项为当前 Windows 无法创建 symlink/reparse 的条件跳过 |
| 权限主链路独立审计 | PASS | 358 passed、1 skipped；未发现残留 P0/P1/P2 绕过；1 项为 Windows symlink/reparse 条件跳过 |
| 后端最终全量回归 | PASS | 打包修复后最终重跑：1118 passed、4 skipped、0 failed；36 条仅为 openpyxl `datetime.utcnow()` 弃用警告；46.79s |
| 前端最终全量回归与构建 | PASS | 53/53 test files、266/266 tests；732.41s；TypeScript 检查与 Vite 构建通过，13,083 modules，25.79s |
| 浏览器页面验收 | PASS | `/quick-analysis`、`/dashboard`、`/data-domains` 实际页面可用；PERSONAL/DOMAIN 标签、数据域过滤与 Local Agent 状态正确，控制台无错误 |
| Local Agent 真实 HTTP 能力 | PASS | loopback-only、8 小时配对令牌；FT 发布 SHA/7200s/64MiB 与登记一致，CP 明确 disabled |
| Release discovery 与解包启动 | PASS | M4 Release 定向 15 passed；与 Local Agent 测试合计 24 passed；解包后真实 `-m local_agent --validate-only` 两次通过，必需文件 11 个、禁止项 0 |
| 真实杰群 FT 520 文件 | PASS | 520 files；3,041,085,645 bytes；6,813,800 records；23 parameters；PAT engine 95.078s；端到端 96.140s |
| 原始文件上传与结果登记 | PASS | `raw upload bytes = 0`；仅登记 7,645-byte PAT 结果，Quick Session 7 为 `LOCAL_AGENT/PERSONAL/SUCCESS`，确认后本机运行目录已清理 |
| Quick 前后正式 Canonical | PASS | `test_run=235`、`unit_result=964,232`、`measurement=17,660,854`，均未因本次 Quick 增加 |
| 静态交付检查 | PASS | 29 个新增 Python 文件 Ruff 通过；PowerShell 启动脚本 AST 与 `-ValidateOnly` 通过；`git diff --check` 无 whitespace error |

真实 520 文件验证复用了登记的 FT 发布工具入口，不生成巨型清洗 Excel；运行完成后本机
受控工作目录按确认流程清理。上述 95.078 秒和 96.140 秒分别为本次 PAT 引擎与完整
Local Agent → TMS 登记链路实测，不是旧流程估算。

## 4. 不确定与尚未完成

1. `DATA_BREAK_GLASS` 当前只保留权限码和授权谓词扩展点，没有可授角色、批准理由、
   短时授权、双人复核或持久审计工作流；生产环境不得启用。
2. 自声明回执没有设备私钥签名、服务端 nonce、一次性任务票据和防重放；签名安装包、
   DPAPI 设备凭证、可信执行隔离及跨两台机器验收仍是生产门。
   当前中央开发候选 Release 包含 Agent 程序，但已发布 FT Cleaner PYZ 仍由受控安装流程
   单独部署并按 SHA 绑定，不把外部 Cleaner 包静默装入 TMS Release。
3. `LOCAL_PATH_SIZE_MTIME_V1` 只使用相对路径、大小和纳秒级 mtime，不包含文件内容哈希；
   同大小、同 mtime 内容替换的 TOCTOU 风险仍开放。
4. FTP/SFTP 凭据接入、目录扫描去重、生产定时调度、异常待办和运行监控尚未实现。本轮
   只完成了权限归属、Source Root 域绑定和双位置 Quick 架构基础。
5. CP 发布工具尚无批准的原始目录低内存 PAT Adapter、输出合同和真实 Golden，因此 CP
   原始目录能力必须继续显示禁用，不得以普通统计/Cp/Cpk 代替。
6. 当前 UI 每次只维护一个待登记运行，失败、确认和重启清理均已有边界；Agent 服务端尚未
   实现持久化最大队列数/工作磁盘配额，故并发压测与磁盘耗尽防护仍是生产容量门。
7. 异步导出采用短事务，不在长时间渲染期间持有数据授权锁。撤权竞态由最终 ACL 阻止成功
   登记和交付，但已经开始的受信计算可能继续；物理文件删除失败还需要生产级 orphan
   扫描、重试和告警闭环。
8. 前端构建仍提示两个压缩前 chunk 超过 500 KiB；最大主 chunk 约 2.38 MiB、gzip 后约
   747 KiB。这不阻断本轮功能，但路由懒加载和图表依赖拆分仍是首屏性能优化项。
9. `config.example.json` 和启动器默认值仍使用当前开发环境的 Windows 路径；它们不是秘密，
   且缺少对应工具时能力会保持 disabled，但正式安装包应在安装时生成本机配置或统一使用
   环境变量，不应把开发机路径当成生产默认值。
10. SQL Server 生产库历史数据域映射、Owner 复核、目标 TEST/UAT、安全、容量和业务签字
   尚未完成；开发候选 PASS 不能替代这些外部门。

## 5. 下一步

1. 在目标 TEST 环境导入实际组织的数据域清单，完成 Owner 审阅、A/B 个人隔离、域成员/
   非成员/过期/撤销和管理员无隐式读取权的签字验收。
2. 设计 FTP/SFTP Source Definition 的凭据引用、扫描水位、幂等去重、调度、异常待办和
   可观测性，再接入第一条华虹 CP 生产源。
3. 为 Local Agent 增加签名安装包、设备密钥、服务端一次性 nonce、防重放和内容级
   Manifest；完成两台机器、断网、长任务、重启和恶意结果容器的生产安全测试。
4. 在 CP 工具仓先形成原始目录只读 Adapter、确定性输出合同和真实样本 Golden，再登记
   CP Local Agent 能力；在此之前保持功能 Gate。
5. 完成目标 TEST 的全量回归、浏览器验收、性能/容量和安全评审后，再决定 G3/G4。
