# TMS v1.0 M1 数据安全完成报告

- 报告日期：2026-08-29
- 验证范围：M1 数据正确性、安全边界、原子发布与可恢复性
- 验证环境：Windows 11 开发机、SQL Server 2014 SP2 Enterprise（12.0.5000.0）、`TMS_G0_DEV`
- Schema Revision：`sql2014_0018`
- 最终全量回归：`FINAL_VERIFICATION_PENDING`

## 1. 结论

M1 计划内的数据安全能力已经实现，并在隔离开发库完成真实数据和故障恢复验证。正式明细唯一事实链仍为 `test.test_run -> test.unit_result -> test.measurement`；开发库在 Migration、原子发布、A5 导出/归档回滚验证前后保持 `139 / 291,127 / 5,578,114` 行，Current Dataset 数为 `10`，没有因安全验证删除或覆盖 Canonical 明细。

本报告只证明仓库实现和 `TMS_G0_DEV` 开发库验证，不代表目标生产环境已经升级、部署或上线。最终交付前仍须将本轮最后一次全量回归结果回填到本报告。

## 2. 做了什么

### 2.1 输入和身份安全

- 正式目录提交改为管理员登记的 Source Catalog、`source_id/root_code + relative_path` 和提交前 Manifest 指纹，不再让普通用户提交任意服务器绝对路径。
- Source Catalog 对越根、`..`、符号链接/reparse point、目录身份变化、文件扩展名和 Manifest 变化失败关闭。
- 前端受保护请求统一使用 Bearer Token；401 统一清理会话；生产认证配置拒绝弱 JWT、内存 Job Repository 和包含密码的数据库 URL。
- Cleaner 子进程只继承允许名单中的环境变量，防止把 Token、数据库密码或无关进程秘密传入厂家工具。
- CP、FT 继续使用独立 Adapter/Writer，不增加格式自动猜测；未知厂家、未知格式、未知单位、未知 Lot/Spec 继续阻断正式发布。

### 2.2 正式事实原子发布

- 新增 `sql2014_0015` staged/finalize 合同：Cleaner 写入准备态的 Run、Dataset Version、来源映射和 finalize intent，最终在一个事务内切换旧/新 Current、结果摘要、Batch、Job 和 intent。
- finalize 使用租约和幂等合同；Cleaner 已完成但最终事务中断时，可直接重放 staged intent，不重复运行 Cleaner。
- 对 7 个事务边界设置故障注入：旧 Current 退位、新 Version 发布、新 Run 发布、结果摘要写入、Batch 完成、Job 完成、intent 完成。每个注入点均证明事务整体回滚。
- 修复开发库历史 Processing Run Current 不一致时使用独立、受审计、可重复执行的修复脚本；未删除 `test.*` 事实。

### 2.3 Lot、Spec、未知值和来源血缘

- CP/FT Writer 按 Lot 解析并绑定 Spec；多个 Lot 无法证明 Spec 相同或同一 Lot 出现冲突时停止发布。
- FT 没有 PASS/FAIL/Bin 的源数据保持 `pass_count=NULL`、`yield_rate=NULL` 和 `UNKNOWN`，API 与页面显示“—”，不补成 0%。
- `processing_run_input_file` 保存 Dataset Current 对应的全部来源文件；Job 详情返回文件名、大小、SHA-256、顺序和 `WRITER_VERIFIED`/接收记录来源，不返回存储 URI。
- 正式入库最终 Manifest 保存 SHA-256；Dataset、Version、Run、Job、Batch、Cleaner Release 和 Source 可双向追溯。

### 2.4 生命周期安全

- 最新 Cleaner 导出使用独立 `EXPORT_LATEST` Job 和临时 Artifact，不创建新 Dataset Version，也不改变 Canonical Current。
- 显式重清洗使用 `REPROCESS_UPDATE` 业务动作，但复用经过验证的 `INITIAL_IMPORT + ATOMIC_V1` Worker/Writer 合同；采用兼容 Format Profile 的最新已发布 Cleaner Release。
- 逻辑归档只改变 Dataset/Version/关联 Current Run/结果摘要状态，不删除 `test.*`、Source、Batch 或 FTP/NAS 原始文件；归档、重清洗和导出均要求权限、理由、幂等键和审计记录。

## 3. 确定的事实

### 3.1 数据库身份和不变性

| 项目 | 已验证结果 |
|---|---:|
| 数据库 | `TMS_G0_DEV` |
| SQL Server | 2014 SP2 Enterprise，12.0.5000.0 |
| Schema | `sql2014_0018` |
| `test.test_run` | 139 |
| `test.unit_result` | 291,127 |
| `test.measurement` | 5,578,114 |
| Published Current Dataset Version | 10 |

以上四个事实计数在 0015～0018 Migration、A5 Export、Archive 回滚和 Reprocess 回滚验证后保持不变。

### 3.2 真实 Current 样本

| Dataset / Job | 厂家与阶段 | 产品 / Lot | Run / Unit / Test Item / Measurement | PASS/FAIL/Yield | 来源血缘 |
|---|---|---|---|---|---|
| Dataset 43 v1 / Job 95 | 华虹 CP | `NCETEN30CAC` / `FA5X-2565` | 25 / 3,875 / 13 / 50,375 | 3,775 / 100 / 97.419355% | 1/1 `WRITER_VERIFIED` |
| Dataset 44 v1 / Job 96 | 日月新 FT | `NCEAP40PT15D(M)-2B00` / `FA59-3997` | 6 / 35,350 / 18 / 636,300 | NULL / NULL / NULL | 6/6 `WRITER_VERIFIED` |
| Dataset 46 v1 / Job 98 | Jetech CP | `C146808.02` / `C146808.02` | 1 / 2,581 / 22 / 56,782 | 2,393 / 188 / 92.716002% | 1/1 `WRITER_VERIFIED` |

三次 Worker 运行耗时分别为 9.699 秒、38.168 秒和 9.880 秒。最终输入 Manifest SHA-256 分别为：

- Job 95：`6d26b0e25a61c5d96461f843c88b46f6ed0b3a0ca672c7368fcdc329e63b7ea2`
- Job 96：`483c55a25ceb048b44b79239ff43a7478fb9c0b17b8532fe5bc43e8842257e5f`
- Job 98：`ecdebb1fd359f8debadf6baca2b608fd5a25eb77a62ac3005c1511d22ec071d4`

### 3.3 A5 非变异和恢复验证

- Export Job 148：Dataset 46、Cleaner Release 11、Parent Job 98；Job `SUCCESS`、Artifact `READY`，生成 3 个临时 Artifact，总字节和 SHA 由最终回归报告记录；导出前后 Canonical 与 Current 计数不变。
- Archive 回滚 E2E：Dataset 46、Dataset Version ID 62；Current View 计数由 `(1, 1, 2,581, 56,782)` 变为 `(0, 0, 0, 0)`，`test.*` 计数不变，Operations 为 `HEALTHY`；外层事务回滚后原状态恢复。
- Reprocess 回滚 E2E：事务内创建 Job 149，Parent 98、Batch 79、Release 11、Profile 11、动作 `REPROCESS_UPDATE`；排队和幂等重放未改变正式事实，事务回滚后 Job 不存在且 Batch/Current 恢复。

## 4. 不确定的和未执行的事项

1. 最终全量后端、前端、构建、静态检查和发布包 SHA 仍须以最后一次干净工作树验证为准：`FINAL_VERIFICATION_PENDING`。
2. 当前实例是 SQL Server 2014 SP2；目标生产环境必须升级到 SP3 和适用安全更新后重新验证。
3. 本轮未在目标 Windows Server 使用正式服务账号执行 ACL、Task Scheduler、HTTPS、反向代理和重启恢复。
4. 本轮 Archive/Reprocess 使用外层事务回滚验证安全语义；生产变更窗口的实际归档和正式重清洗必须由有权业务用户选择批准数据执行。
5. SAP-B1、MES、QMS 仍只有 crosswalk 与接口合同清单，没有自动接口，不得把源文本当作企业物料主数据。

## 5. 验证证据

| 验证项 | 入口或证据 | 结果 |
|---|---|---|
| Schema 一致性 | `scripts/g0/verify_sql2014_schema.py` | 0018 PASS |
| 原子 finalize 故障注入 | `scripts/g0/verify_atomic_finalize_sql_e2e.py` | 7 边界全部回滚，staged 恢复不重跑 Cleaner |
| 真实来源血缘 | Job 95/96/98、`processing_run_input_file` | 1/1、6/6、1/1 `WRITER_VERIFIED` |
| 逻辑归档 | `scripts/g0/verify_a5_archive_sql_e2e.py` | Current 隐藏、事实不删、事务恢复 |
| 显式重清洗 | `scripts/g0/verify_a5_reprocess_sql_e2e.py` | 合同/幂等/血缘 PASS，事务恢复 |
| 最新 Cleaner 导出 | Job 148 / Dataset 46 | 3 Artifact，Canonical 不变 |
| 受控来源和路径攻击 | `tests/unit/test_source_catalog.py` 等 | 专项测试 PASS；最终合计待回填 |
| 最终全量 | 后端、前端、build、Ruff | `FINAL_VERIFICATION_PENDING` |

运行配置通过 `.env.runtime.ps1` 和进程环境注入；报告和命令输出不记录密码、Token、连接串或存储 URI。

## 6. 下一步

1. 主线合并最后修复后执行最终全量回归，替换全部 `FINAL_VERIFICATION_PENDING`。
2. 由 DBA 在目标 SP3 实例执行 pre-check、备份、空库迁移、现有库升级和独立 restore drill。
3. 由 CP/FT、质量和 SAP-B1 主数据 Owner 对 Golden、Spec/Bin/Retest 口径和企业 crosswalk 签字。
4. 只有 G3 测试服务器灰度通过后，才能申请 G4 生产分批；本报告不能作为生产上线证明。
