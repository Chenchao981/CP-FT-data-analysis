# TMS PERSONAL / DOMAIN 与 Local Agent 开发计划 v1.0

- 日期：2026-09-01
- 需求基线：`docs/business/TMS_Data_Ownership_and_Local_Analysis_Requirements_v1.0_2026-09-01.md`
- 发布边界：本轮形成开发候选和本机验收证据，不等同于生产批准
- 本轮状态：M1-M5 已形成开发候选并完成本机回归、浏览器验收与真实 520 文件闭环；目标 TEST/UAT、安全、容量和业务签字仍按完成报告跟踪

## 1. 目标状态

```text
手工上传 / 本机快速分析 -> PERSONAL -> owner only -> 我的数据
FTP/NAS/API/SAP         -> DOMAIN   -> explicit grant -> 数据域 Dashboard

用户电脑目录 -> Local Agent -> 已发布 CP/FT 工具 -> 结果 + 摘要 + Manifest -> TMS
服务器目录   -> Server Worker -> 已发布 CP/FT 工具 -> 结果 + 摘要 + Manifest -> TMS
```

## 2. 开发工作包

### M1：数据访问模型

- 新增 Data Domain、用户授权和 Source Definition；
- Batch/Dataset 增加 `access_scope`、`data_domain_id`、`source_definition_id`；
- 历史数据执行 fail-closed 迁移，不从 PRODUCTION 自动推导共享；
- 建立唯一 SQL 授权谓词：PERSONAL owner 或 DOMAIN active grant 或显式 break-glass；
- 普通业务权限默认开放，控制面权限保持独立。

关闭条件：权限矩阵、直接 URL、列表、图表和导出使用同一谓词；代码不再把 PRODUCTION 当 ACL。

### M2：数据域管理闭环

- 当前用户查询已授权数据域；
- Data Domain 管理员创建/停用数据域，授权/撤销用户；
- `SERVER_CATALOG/QUICK_ANALYSIS` 与 `FORMAL_IMPORT` Source Root/Source Definition 都固定绑定 `data_domain_code`，普通用户不能覆盖归属；
- 手工上传强制 PERSONAL；正式 Writer 从 Batch 复制归属到 Dataset。
- Dataset Version 创建在同一事务中校验 Dataset、Batch、Job、Run 的 Owner、范围、来源和
  业务身份；普通接口不允许为 DOMAIN Dataset 手工拼接版本。
- Saved Analysis 和异步导出在读取敏感上下文及成功登记前复核当前授权，撤权后失败关闭并
  阻止 `SUCCESS`、Artifact 登记和交付；当前 attempt 的物理输出 best-effort 清理，失败时
  进入 orphan retry，不能把文件删除描述成绝对即时保证。

关闭条件：A/B 个人隔离、域成员/非成员/过期/撤销、管理员无隐式数据权全部通过。

### M3：Local Agent 与结果注册

- 保留 `SERVER_CATALOG` 兼容入口；
- 新增 Windows 用户态 Local Agent、本机目录选择、Manifest、能力清单和后台运行；
- FT 杰群 Adapter 校验发布包 SHA 后调用原 PAT 入口；
- 配对令牌从 Agent 启动起有效 8 小时，过期后通过重启 Agent 重新生成；
- TMS 接收端只接收结果，先执行 HTTP 总大小限制，再完成大小/SHA、严格 XLSX 容器白名单、17 列合同、摘要校验与原子登记；
- 回执定义为自声明一致性回执，不称为签名执行证明；
- 失败运行、登记确认后的本机运行和 Agent 启动时的安全 UUID 陈旧目录均有界清理；Agent 重启不恢复中断任务；
- `LOCAL_AGENT` Quick 会话固定为 PERSONAL；`SERVER_CATALOG` Quick 会话继承来源
  `data_domain_code` 并固定为 DOMAIN。两类会话均按结果上限而不是源数据大小预留服务器配额。

关闭条件：单元/API/UI 测试通过；真实 520 文件完成并证明 raw upload=0。

### M4：Quick Analysis 页面重构

- 来源分为“本机目录”和“服务器/FTP/NAS”；
- 先选 CP/FT 固定路线，再选厂家和已登记分析能力，不做自动识别；
- 展示 Agent 在线状态、发布版本/SHA、脱敏目录名、文件数、大小和 Manifest；
- 显示本机运行进度并在完成后只上传结果；
- CP 未满足能力合同前显示明确 Gate，不伪造可运行状态。

关闭条件：浏览器完成选择、确认、运行、登记、历史记录和下载；断开 Agent、令牌错误、Manifest 漂移均给出可理解的失败信息。

### M5：验证、文档与交付

- 后端和前端全量回归；
- SQL 2014 Migration 静态测试、开发库增量升级和随机空库升级；
- 权限 SQL E2E 和查询性能；
- Local Agent 安全负向测试；
- 中央开发候选 Release 纳入完整 Local Agent 程序/示例配置/启动脚本，排除测试、缓存、
  工作目录、真实配置和令牌，并执行解包后 `-m local_agent --validate-only` 冒烟；
- 架构图、运维入口、完成报告和限制清单；
- 检查暂存清单，不提交源数据、结果、日志、缓存、密钥或 Cleaner 包。

## 3. 测试矩阵

| 测试项 | 必须结果 |
|---|---|
| 个人 A 数据，用户 B | 404/403，列表也不可见 |
| Domain D，成员 A | 可读 Published Current，可分析/派生导出 |
| Domain D，非成员 B | 不可见 |
| Domain 授权过期/撤销 | 下一次请求立即不可见 |
| Domain Quick 在排队或运行中撤权/停用账号 | Worker 停止；不登记 SUCCESS Artifact |
| SYSTEM_ADMIN 无 grant | 不可读数据内容 |
| `business_domain=PRODUCTION` | 不产生任何额外数据权 |
| 网页伪造 owner/scope/domain | 服务端忽略或拒绝 |
| A 的 Dataset 关联 B 的 Batch/Run | 统一 404；不创建 Dataset Version，不回显可枚举 ID |
| Saved Analysis/导出排队后撤权 | 执行前和成功登记前复核；无 `SUCCESS`/Artifact/交付；当前 attempt best-effort 清理，失败进入 orphan retry |
| Agent 错误 Origin/Host/token | 拒绝 |
| Agent 工具 SHA 不匹配 | 拒绝运行 |
| Manifest 确认后源目录变化 | 拒绝运行 |
| 结果大小/SHA/XLSX schema 不符 | 不登记 SUCCESS |
| XLSX 含公式/超链接/定义名称/外链/VBA/OLE/嵌入对象 | 不登记 SUCCESS |
| 配对令牌超过 8 小时 | 401；重启 Agent 后重新配对 |
| 失败/登记确认/Agent 重启陈旧目录 | 只清理受控 `work_root/<safe-run-id>`；不得越界 |
| 520 文件 FT PAT | raw upload=0；6,813,800 有效行、23 参数；与发布工具一致 |
| Quick 前后 Canonical | `test.*` 行数不增加 |

## 4. 风险和开放门

- 旧系统把 PRODUCTION 当共享，本次迁移会收紧可见性；生产升级前必须输出影响清单并由数据 Owner 审阅映射。
- 回环 HTTP Agent 只作为开发候选；生产版本优先采用签名 URI Handler + Agent 主动 HTTPS 出站、DPAPI 设备凭证和短期任务令牌。
- 当前回执没有设备签名、服务端 nonce 或防重放，只能证明字段和文件的一致性，不能作为受信设备执行证明。
- `LOCAL_PATH_SIZE_MTIME_V1` 不包含文件内容哈希，仍有同大小、同 mtime 内容替换的 TOCTOU 风险。
- `DATA_BREAK_GLASS` 当前只有权限码和谓词扩展点，没有可授角色、审批理由、短时授权或持久审计工作流，生产不得启用。
- FTP/SFTP 凭据接入、定时拉取、去重调度和异常待办未实现。
- 当前 CP 发布包没有批准的原始目录 Quick PAT；CP 本地路线先完成架构和能力门禁，算法 Adapter 另行 Golden 验收。
- 当前 FT PYZ 缺内嵌 Git SHA，且旧 `release.zip` 落后；本轮只能按单文件 SHA 固定，正式发布前需净化并重建签名 Release。
- `config.example.json` 和启动器的默认 Python/Cleaner 路径面向当前开发机；正式安装器需按
  目标电脑生成配置或使用环境变量，不得把示例路径直接当成生产配置。
- Agent 服务端尚未实现持久化的总运行队列和工作磁盘配额；并发、磁盘耗尽与成功结果 TTL
  仍需目标 TEST 容量验收。
- 异步导出不会在长时间计算期间持有授权锁；撤权发生在执行前复核之后时，受信 Worker
  可能继续计算，但 final ACL 必须阻止成功登记和交付。物理输出清理失败需要 orphan
  扫描、重试和告警闭环。
