# TMS v1.0 Core 仓库交付完成报告

- 报告日期：2026-08-29
- 目标版本：TMS v1.0 Core
- 仓库分支/验证基线提交：`codex/auth-rbac-frontend` / `0dca74a`
- 数据库 Revision：`sql2014_0018`
- 仓库交付结论：**PASS（源码、开发库 G0-G2 与可复现发布包）**
- 生产上线结论：**未上线；G3/G4 未执行**

## 1. 结论

TMS v1.0 Core 的计划范围已经形成完整候选交付：四个固定 CP/FT 正式入口、受控来源、异步 Cleaner/Worker、唯一 Canonical、Dataset Current、工程师检索与 Job 追踪、质量/领导视图、主数据 crosswalk、A5 导出/重清洗/归档、Quick PAT、Worker 运维、清理、发布与备份恢复工具均已落地，并取得真实开发库证据。

最后一次全量回归、认证浏览器 UAT、真实 SQL 复核、秘密/禁止路径扫描和可复现发布包均已通过，因此仓库与开发库 G0-G2 交付判定为 PASS。该 PASS 只表示代码和开发库阶段完成；没有 G3/G4、正式基础设施和业务签字，不能写成“整个系统已生产上线”。

## 2. 做了什么

### 2.1 一线开发/工程师工作闭环

- 固定保留工程 CP、工程 FT、量产 CP、量产 FT 四个入口；厂家和格式必须显式选择，不自动猜测。
- 正式提交使用 Source Catalog 和 Manifest 预检，提交后可查看服务端分页的上传/结果、Job 详情、状态时间线、父子 Job、Cleaner Release、全部来源 SHA 和 Dataset 深链。
- Dataset Current 目录按产品、Lot、厂家、阶段、工程/量产、状态和时间检索，不再要求工程师手填内部 Dataset ID 或查数据库。
- 缺 Lot 走 `NEEDS_INPUT -> 文件级补录 -> 同 Release 子 Job`；权限不足、输入变化、格式错误、Worker 不可用均给出明确状态。
- FT 没有 PASS/FAIL 时保持未知；Quick PAT、临时 Workspace 和正式入库三条边界在页面和数据模型中分开。

### 2.2 数据正确性和追溯

- `test.test_run -> test.unit_result -> test.measurement` 是唯一正式明细链。
- Lot 级 Spec、全部来源映射、staged + 原子 finalize 和 Current 唯一性关闭了跨 Lot 误用、首文件血缘丢失和半发布窗口。
- Source、Receipt、Batch、Job、Cleaner Release、Processing Run、Dataset Version 和 Artifact 均有可审计关联；源文件和 Manifest 使用 SHA-256。
- 最新 Cleaner 导出不改变 Canonical；显式重清洗创建新版本；逻辑归档保留历史事实并使 Current View 不再展示归档版本。

### 2.3 质量和领导视图

- 提供按时间、产品、Lot、厂家、阶段筛选的 Current Dataset、产量、已知良率、Fail Bin、未知占比、异常任务和数据新鲜度。
- 良率只以已知 PASS+FAIL 为分母；未知 Unit 单独显示，避免把缺失数据当成 0 或好品。
- 指标可下钻到 Dataset、Job 和 Source；管理读权限与一线 Owner 权限分离。
- 建立源观察产品/供应商到 SAP-B1 企业主数据的 PENDING crosswalk，只有治理角色可批准；没有自动把厂家文本升级为正式物料。
- 输出 SAP-B1/MES/QMS 接口合同清单，明确 Owner、键、版本、安全、对账和上线门，不制造尚不存在的接口。

### 2.4 运维和发布

- Worker registry 保存启动、心跳、Database/Server/Schema 身份和 drain 状态；管理员可安全 Drain/Resume，探针不以“进程存在”代替业务就绪。
- Quick 与 Formal Artifact Cleanup 分成两个计划任务，默认 DryRun；精确根、TTL、租约、永久 Artifact、symlink/reparse point、容量和审计均失败关闭。
- 生产配置样板拒绝弱 JWT、密码 URL、根目录重叠和 Schema 不一致；日志轮转并脱敏 Token/密码 URL。
- 提供 API、Worker、QuickCleanup、FormalCleanup 四个 Windows 计划任务的安装、状态、卸载和健康检查。
- 提供 Migration pre/post-check、COPY_ONLY/CHECKSUM 备份、VERIFYONLY、白名单 restore drill、空库迁移和可复现 ZIP 发布 Runbook。

## 3. 确定的事实

### 3.1 里程碑完成情况

| 里程碑 | 已完成范围 | 当前判定 |
|---|---|---|
| M0 | 生产就绪计划、风险、门禁和完成定义 | 完成 |
| M1 | Token、Source Catalog、Lot-Spec、NULL Yield、原子 finalize、全部来源血缘 | 实现与专项验证完成 |
| M2 | Dataset Current、分页筛选、Job 详情、深链和工程师闭环 | 实现与专项验证完成 |
| M3 | 领导/质量 KPI、未知口径、下钻、crosswalk、接口合同 | 实现与专项验证完成 |
| M4 | A5 生命周期、Worker 运维、清理、发布、备份恢复工具 | 实现与专项验证完成；目标机外部门未执行 |
| M5-M7 | 全量回归、G0-G2、报告和安全交付 | 完成，G0-G2 PASS；G3/G4 未执行 |

### 3.2 开发库事实

- `TMS_G0_DEV` 当前为 `sql2014_0018`；正式事实为 139 Test Run、291,127 Unit Result、5,578,114 Measurement、10 个 Published Current Dataset Version。
- 华虹 CP Dataset 43、日月新 FT Dataset 44、Jetech CP Dataset 46 已真实发布并具备 1/1、6/6、1/1 Writer-verified 来源血缘。
- 日月新 Dataset 44 的 35,350 Unit 没有 PASS/FAIL；系统和管理视图保持良率 NULL，而不是 0%。
- Export Job 148 对 Dataset 46 生成 3 个临时 Artifact；最终浏览器 UAT 的 Job 157 再次得到 `SUCCESS/READY/PRESENT` 并触发下载，正式事实和 Current 不变。
- Archive 和 Reprocess 真实 SQL 演练均在外层事务中完成验证并恢复，未替业务用户做永久决策。
- 随机空库已从 0001 升级到 0018，并精确清理；目标生产备份/恢复仍未执行。

### 3.3 对新洁能的实际价值

- 工程侧：从厂家文件到 Lot/Dataset/图表有统一入口、失败原因和追溯链，减少人工找文件、手填 ID 和口径不一致。
- 质量侧：能区分“已知不良”和“源数据没有判定”，避免错误良率进入供应商或产品比较。
- 管理侧：可以从产量、良率、未知占比和异常任务下钻到正式来源；数字不脱离样本数和时间范围。
- IT 侧：Cleaner 不重写、SQL 写入可恢复、权限和主数据升级需要批准、部署/清理/备份均有失败关闭合同。
- SAP-B1 协同：当前先解决可审计 crosswalk 和接口责任，不在物料主数据尚未治理时直接把测试厂文本写入 ERP。

## 4. 不确定的和未执行的事项

### 4.1 最终仓库门

- 后端：`393 passed, 1 skipped, 4 warnings in 34.82s`；前端：25 files / 91 tests PASS；TypeScript + Vite 生产构建 13,055 modules、24.03s PASS。
- Ruff：全仓 `F/E9` 为零，本次新增/修改 Python `F/I` 为零；25 个范围外历史 `I001` 作为 P2 保留。
- 认证浏览器：SYSTEM_ADMIN、MANAGER_VIEWER、QUALITY_ENGINEER、CP_ENGINEER 四角色 PASS；直接 URL 越权、A5、质量、crosswalk、Operations 与 Worker 均已覆盖，console 无 warning/error。
- 发布包：`NCE-TMS-v1.0-core.zip`，481,749 bytes、220 个 Manifest 文件、Schema `sql2014_0018`；双构建字节一致，SHA-256 `D84E7BCC1CDADDAE19C6ADEFD694EB32AD605FAA6C79CF2BDE7E500A54D9D9DC`。
- 暂存清单与秘密扫描为 165 files / 0 issue；没有 `.env.runtime.ps1`、原始数据、Artifact、日志、账号、缓存或 `.remember/`。最终 docs-only 回填不改变已测试程序内容。

### 4.2 G3/G4 外部门

| 未执行事项 | 需要的负责人和证据 |
|---|---|
| SQL Server SP3 与安全更新 | DBA/IT 安全；版本、补丁、兼容与性能复验 |
| 生产备份和独立恢复 | DBA；备份校验、restore 耗时、RPO/RTO、post-check |
| 服务账号、ACL、计划任务 | Windows 管理员；最小权限、重启、重试、卸载证据 |
| HTTPS、反向代理、DNS、防火墙 | 网络/安全；扫描和访问策略 |
| AD/OIDC 或正式账号角色 | CIO/信息安全/部门负责人；权限矩阵和越权 UAT |
| CP/FT Golden 与业务口径 | CP/FT 工程、质量；Lot/Spec/Bin/Retest 签字 |
| SAP-B1/MES/QMS 接口 | 主数据 Owner、SAP/MES/QMS Owner；合同、对账和回退 |
| 生产灰度与最终上线 | 生产、质量、IT 运维、CIO；G3/G4 报告和签字 |

## 5. 验证证据

- M1 数据安全：`docs/development/TMS_v1.0_M1_Data_Safety_Completion_Report_2026-08-29.md`
- G0-G2 灰度：`docs/development/TMS_v1.0_Gray_Release_Report_2026-08-29.md`
- 全量回归：`docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md`
- M4 验证：`docs/operations/TMS_M4_Production_Readiness_Verification_Report_2026-08-29.md`
- Windows/数据库 Runbook：`docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md`
- SAP/MES/QMS 合同：`docs/architecture/TMS_SAP_MES_QMS_Interface_Contract_Checklist_v1.0_2026-08-29.md`

## 6. 下一步

1. 由新洁能批准 G3 的服务器、账号、产品/Lot 范围、人员和回退阈值；完成 SP3、备份恢复、HTTPS、ACL 和 Task Scheduler。
2. G3 建立质量驾驶舱响应时间与前端包体 SLO，关闭已记录的性能 P2 后再做并发/连续运行观察。
3. G3 连续观察和业务 UAT 通过后再申请 G4；G4 必须分厂家/阶段扩围并保留回滚能力。
4. 后续优先做正式主数据和 SAP-B1/MES/QMS 合同评审，不先扩展未经 Golden 验证的新厂家。
