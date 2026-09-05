# NCE PYMS 数据库结构与阶段字段整改完成报告

日期：2026-09-05。范围：用户确认数据库为测试数据后，按现有业务合同同步调整数据库、入库和分析读取。验收对象为本机测试环境与 `TMS_G0_DEV`，不是生产上线验收。

## 做了什么

1. 数据库升级至 `sql2014_0027`。新增 `test.cp_run_detail`、`test.ft_run_detail`，将原 JSON 中的 CP 晶圆来源、FT Source_ID、制造批次、测试标签、来源文件及运行规格引用转为结构化字段。
2. 增加运行阶段和规格阶段复合外键、FT 来源非空约束、来源及制造批次索引。不同测试运行允许相同来源；未按坐标清除测试数据，也未猜测重测次数。
3. 三个入库 Writer 在原事务内写入阶段详情；正式分析、参数关联、散点图、正式规格上下文和导出来源识别同步读取关系字段。历史 JSON 保留为证据快照。
4. 增加四个 CP/FT 查询视图。它们分隔阶段字段和行，但不是独立物理测量表。`test.unit_result / test.measurement` 继续作为共享事实源，保持 ID、Dataset Version、评价及追溯外键。
5. 同步 API/Worker/导出/发布脚本、验证脚本和运维文档的目标结构版本，恢复保留认证的测试环境。

业务字典及设计边界见 [数据库阶段字段合同](../architecture/NCE_PYMS_Database_Stage_Fields_2026-09-05.md)。这次采用共享测量核心加独立运行信息的方案，未执行原扩展指南中全部物理测量分表计划。

## 确定的：验证证据

| 项目 | 结果 |
|---|---|
| SQL Server 2014 空库全链升级 | 0001 → 0027 通过；随机临时验证库已由原验证脚本清理 |
| 升级前备份 | COPY_ONLY + CHECKSUM；RESTORE VERIFYONLY WITH CHECKSUM 通过；未做完整恢复演练 |
| 现有库字段预检查及回填 | CP 142 个运行、FT 100 个运行全部通过，来源证据与关系字段逐项一致 |
| 升级前后单位记录 | 1,002,163 条，数量及按 Run/状态分组的 ID 范围、摘要一致 |
| 升级前后测量记录 | 18,353,936 条，数量及按 Run/测量状态分组的 ID 范围、摘要一致 |
| Dataset / 评价 | 35 个 Dataset Version、795,065 条测量评价、6,456 条 Bin 评价，数量保持一致 |
| 数据库约束实测 | 跨阶段 Run、跨阶段 Spec、空 FT Source_ID 均被数据库拒绝 |
| Writer 字段回放 | CP/FT 实际字段持久化函数与迁移回填一致，探测事务全部回滚 |
| 后端及本地程序全量回归 | 1,211 passed、4 skipped，51.30 秒；最后字段边界补充后定向 45 项通过 |
| SQL 功能与参数查询 | 现有功能只读复核通过；最终 CP/FT 参数分析复核通过 |
| CSV 导出抽样 | CP Dataset 11/V1、FT Dataset 23/V1 各导出 10 行通过，文件 SHA 对账通过 |
| 代码静态检查 | 修改的业务 Python 文件及新增迁移/核验脚本 Ruff 检查通过；Git diff whitespace 检查通过 |
| 测试环境 | API、Worker、页面全部就绪，数据库守卫 `TMS_G0_DEV / sql2014_0027`，认证保持 CONFIGURED |

数值摘要采用 BINARY_CHECKSUM 聚合，是变化检测证据，不是密码学级逐值证明。迁移本身不更新或复制原 Unit/Measurement；字段对账是全部 242 个运行的逐项核对。四个阶段视图的 Unit 覆盖数量也已核对。

本地证据均在 `artifacts/runtime`：`stage-schema-before.json`、`stage-schema-after.json`、`stage-schema-probes.json`、`stage-schema-backup.json`、`stage-schema-empty-migration.json`、`stage-schema-final-tests.log`、`stage-schema-functional.json`、`stage-schema-final-parameters.json`、`stage-schema-export-cp.json`、`stage-schema-export-ft.json`、`stage-schema-runtime-ready.json`。备份的 SQL Server 端路径记录在本地备份证据文件，不把备份、测试输出、配置或账号提交 Git。

## 做得好的地方

- 先核对真实运行字段再迁移，发现 12 个历史 FT 运行没有 source_file，明确保持 NULL，未从 Source_ID 猜测文件名。
- 运行身份按阶段分开；制造批次、来源元数据 Lot、业务 Lot 不相互覆盖。原始规格引用增加关系约束，但不改变正式规格审批和统计判定语义。
- 使用带数据升级、空库升级、真实数据库拒绝测试和前后对账共同验证，没有仅凭单元测试宣布数据库完成。

## 不确定的与下一步

1. 公共 Unit/Measurement 物理表仍共享。后续若需要独立物理 CP/FT 测量表，必须另行处理评价/追溯外键和容量、并发、迁移切换；不能把本次视图称为分表完成。
2. 新增索引尚未进行专门的前后性能实验，不报告提速倍数或容量上限。
3. 本次没有重新执行所有厂家原始文件上传，也没有完成登录后浏览器端到端验收；写入实测覆盖新字段函数的真实数据库事务回放，读取覆盖现有数据库及导出抽样。
4. 原有 3 个 NEEDS_INPUT 测试任务未取消、未自动补数据。业务重测、重复上传与重复清洗继续使用已有任务和版本边界，历史重复坐标保持原样。
5. 目标服务器、第二台员工电脑、完整备份恢复演练仍须单独验收；FTP、SAP、工单和 AI 延期范围不变。

## 可复现入口

```powershell
. ./.env.runtime.ps1
./.conda-env/python.exe scripts/g0/verify_stage_run_schema.py --probe-writes --output artifacts/runtime/stage-schema-check.json
./.conda-env/python.exe -m pytest tests/unit local_agent/tests -q
./scripts/windows/get_tms_local_test_status.ps1 -AsJson -RequireReady
```

`--probe-writes` 只允许测试库，所有事务回滚。对生产库不要照搬测试命令或假定本地备份路径可访问。
