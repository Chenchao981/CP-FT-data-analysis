# NCE PYMS CP/FT 物理分表完成报告

日期：2026-09-05。范围：接续当天运行字段整改，完成真实测试库的 CP/FT 器件及测量物理分表、写入切换、历史对账和运行恢复。当前结构版本为 `sql2014_0028`。

本报告继承 [0027 运行字段整改报告](NCE_PYMS_Database_Stage_Fields_Completion_Report_2026-09-05.md)，补齐其尚未执行的物理事实迁移。用户已确认现有重复记录来自多次测试；本次保留这些记录，不执行历史去重或推断重测次数。

## 1. 做了什么

- 建立 `test.cp_die`、`test.ft_device`、`test.cp_measurement`、`test.ft_measurement` 四张活动物理事实表。FT 器件表不包含 Wafer/X/Y 列，CP 保留晶圆坐标及来源证据。
- 共享 Source、Dataset、任务和参数管理；继续使用 0027 的阶段运行字段与规格关联。增加两张仅含 ID/阶段的登记表，使原 Bin、测量评价和追溯关系继续使用原生外键。
- 三条现有 Writer 统一通过 StageFactRepository 分配全局 sequence ID，并在原事务中写入相应阶段表。Cleaner、单位换算、规格判定及发布规则沿用现有实现。
- 旧公共名称改为兼容查询视图；四个阶段视图直接读取对应物理表。刷新活动视图绑定，避免读取重命名后的历史表。API 权限、版本与阶段限制继续保留。
- 原表保留为 `*_legacy_0027` 只读快照。兼容视图拒绝旧 INSERT/UPDATE，受控 DELETE 同步删除阶段事实和 ID 登记，仍受评价/追溯外键保护。
- 同步运行启动、API、运维与验证脚本的结构版本要求，补充迁移容量检查、物理合同及验证入口。

## 2. 确定的迁移结果

实际目标为 SQL Server 2014 测试库 `TMS_G0_DEV`。迁移在停写状态执行；成功尝试耗时 **339.262 秒**，包含全量复制、索引、外键切换和逐行原始列比对。此耗时不包含备份、第一次失败处理及后续验证。

| 阶段物理表 | 迁移提交后的原记录数 | 真实样本验证后的记录数 |
|---|---:|---:|
| cp_die | 38,422 | 42,297 |
| ft_device | 963,741 | 999,091 |
| cp_measurement | 580,830 | 631,205 |
| ft_measurement | 17,773,106 | 18,409,406 |
| 器件合计 | 1,002,163 | 1,041,388 |
| 测量合计 | 18,353,936 | 19,040,611 |

提交前逐行比较旧表与新查询合同的全部原始列，包括 ID、状态、创建时间、原始文本和数值。文本以二进制比较，保留大小写、尾部空格及 NULL 差别；浮点值比较存储位模式。另行按 Run、Lot、Pass、测量数量及 Dataset Version 对账。

原有 **35 个 Dataset Version、242 个 Test Run** 在迁移及后续真实导入后均保持原记录内容；新增的 2 个版本、31 个 Run 单独核算。原有 795,065 条测量评价和 6,456 条 Bin 评价在迁移切换时数量不变；新增导入分别增加 686,675 和 3,875 条评价。所有外键启用且受信任，活动视图无历史快照依赖。

## 3. 实际验证

| 验证 | 结果及覆盖 |
|---|---|
| 后端与 Local Agent 回归 | 1,225 passed、4 skipped，51.00 秒；68 条既有依赖警告，跳过项不算通过 |
| 最后定向回归 | Writer、ID 分配及结构版本合同 46 passed；随后加强阶段路由断言，12 项再次通过 |
| SQL 空库迁移 | 从 0001 到 0028 全链路通过；12 个关键对象检查通过，临时库已清理 |
| 实际数据库结构 | 78 张表、10 个视图；物理表、全局 ID 登记、FT 列边界、阶段字段和视图覆盖通过 |
| 并发与拒绝路径 | CP/FT 并发 ID 不重复；兼容读取/删除、快照只读、跨阶段外键、悬空追溯、旧 INSERT/UPDATE 拒绝通过；检查指定原生错误码，探测事务回滚 |
| Bin 与规格评价 | MATCHED/NO_MATCH/CONFIG_AMBIGUOUS 及六种规格评价状态、重复物化幂等性通过；夹具事务回滚 |
| 华虹 CP 正式入库链 | Batch 169 / Job 256 / Dataset 120 v1；25 片晶圆、3,875 个 Die、13 个参数、50,375 条测量，Pass 3,775 |
| 日月新 FT 正式入库链 | Batch 170 / Job 257 / Dataset 121 v1；6 个来源 Run、35,350 个器件、18 个参数、636,300 条测量 |
| 查询复验 | CP/FT 明细分页、版本比较、统计与独立 SQL 对账通过；未知良率仍为 NULL |
| 导出复验 | CP、FT 历史版本各导出 10 行 CSV，行数和文件 SHA256 校验通过 |
| 运行状态 | 页面/API/Worker 均 ready，认证 CONFIGURED；API 与 Worker 同库、同服务器、同 0028 版本；无 QUEUED/RUNNING，原 3 个 NEEDS_INPUT 保留 |

真实导入使用既有正式 Cleaner 包、来源目录预览、上传 API、SQL 队列、Worker 和原子发布流程。验证脚本通过 TestClient 注入已有授权主体，因此证明 API/Worker/数据库链路，不声称覆盖浏览器登录流程。原始样本只读，新增测试版本保留用于复验。

参数分析使用迁移前后同一 CP/FT 候选，返回结果摘要 SHA256 一致，独立 SQL 数量和统计对账通过。SQL 浮点聚合按原验证器 1e-9 容差核对，聚合摘要不要求不同扫描顺序下字节相同。

| 参数分析范围 | 迁移前首次 / 一次温热调用 | 迁移后首次 / 一次温热调用 |
|---|---:|---:|
| CP：7,356 个数值 | 568.767 / 569.466 ms | 610.684 / 568.363 ms |
| FT：86,026 个数值 | 1,668.928 / 1,719.975 ms | 1,470.043 / 1,551.445 ms |

本次只做一次温热采样，未清空服务器缓存，期间还有其他验证工作；结果用于发现功能和明显耗时退化，不作为性能提升比例或生产 SLA。未批准的高级分析规则仍按原规则拒绝，没有为验证放开门禁。

## 4. 容量问题、处理与备份

第一次实际迁移在创建 CP 测量索引时遇到 SQL 1101：SQL Server 所在 C 盘空间不足。事务已完整回滚，原结构仍为 0027，原事实及版本快照对账通过。随后发现先前客户端本地磁盘空间不能代表远程 SQL Server 容量，这是本次前置检查的不足。

处理仅针对测试库：在 **SQL Server 服务器** 的 `D:\TMS_Data` 增加 12 GiB 数据文件和 16 GiB 日志文件，后续按 512 MiB 增长；关闭原 C 盘文件自动增长，回收失败尝试留下的可回收空闲增长。保留 FULL 恢复模式，不删除原始数据或备份。之后增加服务器卷容量检查，再次迁移成功。

本次保留历史快照和全局 ID 登记，**增加存储占用**。迁移前数据页实际使用约 4,599 MiB，成功迁移后、真实导入前约 9,802 MiB；真实导入后约 10,173 MiB。最后检查服务器 C 盘剩余约 7,142 MiB、D 盘约 60,952 MiB；这是当时快照，后续部署须重新测量。D 盘新增 28 GiB 为预分配文件大小，不等于实际数据页使用量。

容量估算检查不替代对 data/log/tempdb/备份及其他数据库的容量规划。单次失败空间回收不应作为日常收缩数据库的策略。

迁移前、迁移后均完成 COPY_ONLY/CHECKSUM 备份和 RESTORE VERIFYONLY。升级后备份为服务器路径 `D:\TMS_Data\TMS_G0_DEV_after_0028_20260905T052723Z.bak`，备份及校验耗时 15.709 秒；FILELISTONLY 确认 2 个数据文件、2 个日志文件均在备份中。没有执行完整恢复演练，VERIFYONLY 不等于恢复验收。

回退需使用备份及匹配代码或前向修复，并处理切换后的新增数据；禁止简单切回只包含迁移前事实的历史快照。正式恢复应为全部逻辑文件逐项指定 MOVE。

## 5. 做得好的、局限与下一步

全量原始列对账、事务切换和 ID 保留使迁移失败可以恢复到原结构，也使评价和历史版本保持可追溯。验证覆盖了真实 CP/FT 新写入、历史查询及导出，避免只验证空表结构。初次磁盘判断不充分已经落实为服务器容量前置检查。

仍未覆盖：生产服务器部署与业务 UAT、完整备份恢复演练、多用户持续负载、浏览器登录全流程，以及本次两个样本之外的全部厂家格式实跑。完整自动测试已覆盖现有合同，但不能把两个厂家实跑扩大为六家全部真实样本验收。本次没有前端代码变更，未重复执行前端测试与构建。

后续生产准备应先做独立恢复演练和预计峰值数据规模压测，再制定历史快照保留/清理周期。旧快照当前继续保留；其清理不属于本次任务。FTP、SAP、工单、AI 和第二台电脑验收仍按既有规划处理，不列为数据库分表未完成项。

## 6. 复验入口与本地证据

结构合同见 [CP/FT 物理存储合同](../architecture/NCE_PYMS_Physical_Stage_Storage_2026-09-05.md)。在已加载运行配置、确认目标为开发库后，使用仓库 Python：

```powershell
. .\scripts\windows\TmsRuntime.Common.ps1
Import-TmsRuntimeConfig -Path (Join-Path $PWD '.env.runtime.ps1')
& .\.conda-env\python.exe scripts\g0\verify_physical_facts.py --probe-writes --output artifacts\runtime\physical-recheck.json
& .\.conda-env\python.exe scripts\g0\verify_stage_run_schema.py --output artifacts\runtime\stage-recheck.json
& .\scripts\windows\get_tms_local_test_status.ps1 -AsJson -RequireReady
```

升级前的 `--before` 基线只适用于 0027；新导入后的总数量不能直接要求与旧基线相等。现库复验应先区分历史记录和新增版本。

本地 `artifacts/runtime` 留存：physical-before/after/final、physical-migration、physical-rollback-verified、physical-import-reconciliation、physical-stage-final、physical-before/after-parameters、physical-functional、physical-cp/ft-import、physical-export-cp/ft、physical-bin/spec、physical-empty-migration-final、physical-backup/post-backup、physical-storage-final、physical-runtime-ready，以及后端/定向测试日志。这些证据及源数据不提交 Git；报告保留必要汇总和可重复入口。
