# TMS v1.0 Core 灰度验证报告

- 报告日期：2026-08-29
- 灰度范围：G0 本机开发、G1 本机认证角色、G2 `TMS_G0_DEV`
- 未执行范围：G3 测试服务器小组试用、G4 生产分批
- Schema Revision：`sql2014_0018`
- 最终 Gate 状态：`FINAL_VERIFICATION_PENDING`

## 1. 结论

本轮已经完成 G0-G2 所需的主要功能验证：受控 Source Catalog、四个固定 CP/FT 入口、普通角色权限、真实 CP/FT Cleaner/Worker/Canonical/Dataset Current、管理质量视图、Worker 运维、最新 Cleaner 导出、逻辑归档、显式重清洗和 Quick PAT 均取得开发环境证据。最后一次合并后全量自动化、最终认证浏览器回归和发布包 SHA 尚待统一收口，因此 Gate 总状态暂记为 `FINAL_VERIFICATION_PENDING`。

G3 和 G4 没有执行。没有目标服务器、正式服务账号、HTTPS、生产数据库备份恢复、业务 UAT 和发布签字时，严禁将本报告表述为“已生产上线”。

## 2. 做了什么

### 2.1 G0：本机开发验证

- 执行后端单元/合同测试、前端组件/API 测试、生产构建、Migration 静态检查、PowerShell AST 与安全配置负向测试。
- 在随机、精确白名单临时数据库从空库执行 `sql2014_0001 -> sql2014_0018`；验证 head、表结构和 Current 一致性后精确删除临时库。
- 对 staged/finalize 的 7 个事务边界执行故障注入，证明回滚和重放不会产生半发布 Current。
- 对路径穿越、reparse point、弱 JWT、带密码连接串、越权访问、过期 Token、未知良率和不兼容 Cleaner 等失败场景执行合同测试。

### 2.2 G1：本机认证角色验证

- 以配置认证模式验证系统管理员、管理/质量角色、CP 工程师等权限边界。
- 管理员可看四个正式入口、Source Catalog Manifest 预览、提交指纹、Operations 与 Worker 状态。
- 管理/质量角色可读取授权的 Current Dataset 和质量 KPI；crosswalk 读取与批准权限分离，未替业务 Owner 批准真实 SAP 映射。
- CP 工程师看不到运维/管理入口，直接访问无权页面得到 Unauthorized；Dataset API 和页面均执行相同服务端数据范围。
- 日月新 FT 无 PASS/FAIL 时页面显示“—”，没有显示 0%；浏览器控制台检查未发现错误。测试账号在验收后禁用或清理。
- A5 和最后一轮主线合并后的完整浏览器复跑结果仍待回填：`FINAL_VERIFICATION_PENDING`。

### 2.3 G2：开发库真实样本与回滚演练

- 真实执行华虹 CP、日月新 FT 和 Jetech CP，核对 Lot、Run、Unit、参数、Measurement、PASS/FAIL/Yield、全部来源血缘和最终 Manifest。
- 执行 Dataset Current 目录、服务端分页/筛选、Job 详情、URL 深链、管理质量摘要和 Operations 一致性查询。
- 固定执行一次非变异最新 Cleaner 导出；归档和显式重清洗使用外层事务做真实 SQL 安全演练，验证后回滚，不对真实 Dataset 做永久业务决策。
- 执行 520 文件 Quick PAT，验证低内存计算、Artifact/Manifest/TTL 和 `test.*` 零增长。

## 3. 确定的事实

### 3.1 灰度级别状态

| 级别 | 环境 | 已有证据 | 当前状态 |
|---|---|---|---|
| G0 | 本机开发 | 自动化、构建、空库 Migration、故障注入、发布合同 | 主要证据 PASS；最终全量 `FINAL_VERIFICATION_PENDING` |
| G1 | 本机认证模式 | Token、角色菜单、服务端越权拒绝、四入口、FT NULL Yield | 已执行；A5 最终浏览器复跑 `FINAL_VERIFICATION_PENDING` |
| G2 | `TMS_G0_DEV` | 三组真实 Current、A5 E2E、Quick PAT、回滚恢复 | 主要证据 PASS；最终 Gate `FINAL_VERIFICATION_PENDING` |
| G3 | 测试服务器 | 需要正式测试机、账号、HTTPS、UAT | **未执行** |
| G4 | 生产分批 | 需要变更窗口、监控、回滚阈值和签字 | **未执行** |

### 3.2 真实 CP/FT 对账

| Dataset | Batch / Job | 产品 / Lot | 事实规模 | 良率口径 | Worker 耗时 |
|---|---|---|---|---|---:|
| 43 v1 | 76 / 95 | `NCETEN30CAC` / `FA5X-2565` | 25 Run，3,875 Unit，13 Item，50,375 Measurement | 3,775 Pass、100 Fail、97.419355% | 9.699 s |
| 44 v1 | 77 / 96 | `NCEAP40PT15D(M)-2B00` / `FA59-3997` | 6 Run，35,350 Unit，18 Item，636,300 Measurement | PASS/FAIL/Yield 均为 NULL/UNKNOWN | 38.168 s |
| 46 v1 | 79 / 98 | `C146808.02` / `C146808.02` | 1 Run，2,581 Unit，22 Item，56,782 Measurement | 2,393 Pass、188 Fail、92.716002% | 9.880 s |

所有输入均为只读受控副本。来源 SHA 和 Writer 血缘如下：

| Dataset | 顺序 | 文件大小 | SHA-256 | 血缘 |
|---|---:|---:|---|---|
| 43 | 1 | 135,184 | `948300fbdb82803d45d9009d6d045ac868d239c1105d4ee5ecbad75d2213b8d0` | `WRITER_VERIFIED` |
| 44 | 1 | 1,442,521 | `f1517356864802649de5558cb823e9ba6813e4ab5bd22fd25a27dca4124dc16a` | `WRITER_VERIFIED` |
| 44 | 2 | 1,436,384 | `1e1391123505fd1a07133364c56db8431d5cc94a453c72fd749dce34e4a99f96` | `WRITER_VERIFIED` |
| 44 | 3 | 1,436,820 | `6d0021aabcb6d534b78a7522523c17f33de20d9fbfbf0404df35c72f897bcdfd` | `WRITER_VERIFIED` |
| 44 | 4 | 1,443,537 | `db030d39159a43056030e9815fc17caeda95615fb3d38d1114237732954976fd` | `WRITER_VERIFIED` |
| 44 | 5 | 1,438,892 | `bb434dd730ada07b4e24369bf8a8d98fd89753d57c8ffd730b093a765143d4c3` | `WRITER_VERIFIED` |
| 44 | 6 | 861,437 | `297d45608614a888c55d36af15e6e17eddeac6520de272c1b9551e124be70b0d` | `WRITER_VERIFIED` |
| 46 | 1 | 1,123,840 | `237699dda0e12fc2298cd650d7bd6d993396c4c207428b271ca2199d83fee568` | `WRITER_VERIFIED` |

### 3.3 A5 灰度证据

| 动作 | 真实验证 | Canonical/Current 结果 |
|---|---|---|
| 最新 Cleaner 导出 | Job 148，Dataset 46，Release 11，Parent 98，`SUCCESS/READY` | `139 / 291,127 / 5,578,114 / Current 10` 不变 |
| 逻辑归档 | Dataset 46，Version ID 62，Current View `(1,1,2581,56782) -> (0,0,0,0)` | `test.*` 不变；Operations `HEALTHY`；外层事务回滚恢复 |
| 显式重清洗 | 事务内 Job 149，Parent 98，Batch 79，Release/Profile 11，`REPROCESS_UPDATE` | 排队不改事实；幂等重放不重复建 Job；外层事务回滚恢复 |

Export Job 148 的 3 个临时 Artifact：

| 角色 | 字节 | SHA-256 |
|---|---:|---|
| cleaned | 411,444 | `6803f04ce85013252a779ce860fa71f9269eaecfefeba4501cdcef91ed3b375c` |
| yield | 174 | `c313d17b562b518de38d6b1bf25306a365239b69f34fdde7951a242e0aec0ba6` |
| spec | 460 | `0d16ff9c8719932b13880da85b43740c02f55ca50a98ad0fb86ebe46ce18c5f3` |

### 3.4 Quick PAT 性能与边界

- 输入：520 个 CSV、3,041,085,645 bytes。
- 解析：6,813,800 records、23 个参数。
- 最近一次直接计算耗时：91.894 秒；这是本链实测，不用于推断未实跑旧流程的加速百分比。
- 计算产物位于独立 Quick Workspace，保留 Manifest、统计摘要、Artifact 和 TTL；正式 `test.*` 计数前后不变。
- 旧链未完整跑完，因此不能给出“提升百分比”；只能确认新链取消了原文件上传和 681 万行正式明细入库两个前置步骤。

## 4. 不确定的和未执行的事项

1. 最后一次全量自动化、前端 build、浏览器 A5 回归和 release SHA：`FINAL_VERIFICATION_PENDING`。
2. G1 使用的是本机临时测试账号，不是企业 AD/OIDC 或正式组织角色。
3. G2 是开发库，SQL Server 仍为 SP2；不能替代目标 SP3 环境并发、容量、备份和恢复表现。
4. 本轮没有让业务用户永久归档或永久重清洗真实 Dataset；这是有意保留的业务授权边界。
5. 未执行跨机器网络、HTTPS、反向代理、服务账号、正式共享目录 ACL、计划任务重启和连续运行观察。

## 5. 验证证据

- 数据安全报告：`docs/development/TMS_v1.0_M1_Data_Safety_Completion_Report_2026-08-29.md`
- 回归测试报告：`docs/development/TMS_v1.0_Regression_Test_Report_2026-08-29.md`
- M4 生产就绪验证：`docs/operations/TMS_M4_Production_Readiness_Verification_Report_2026-08-29.md`
- 生产部署/备份/恢复 Runbook：`docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md`
- A5 SQL E2E：`scripts/g0/verify_a5_archive_sql_e2e.py`、`scripts/g0/verify_a5_reprocess_sql_e2e.py`
- Quick PAT 历史完整证据：`docs/development/TMS_Quick_Analysis_Q0_Completion_Report_2026-08-26.md`

## 6. 下一步

1. 关闭最后 P1、执行统一全量和认证浏览器回归，将最终 Gate 从占位状态改为明确 PASS/FAIL。
2. G3 只选择一个业务小组、有限厂家/产品/Lot；先验证 SP3、HTTPS、账号、ACL、备份恢复和回退阈值。
3. G3 稳定且业务签字后再提交 G4 申请；G4 先单厂家/单阶段，不一次性开放全部范围。
4. 任何错误 Current、越权、源文件变化、不可恢复 Job 或备份恢复失败都应停止扩围并回退。
