# NCE PYMS CP/FT 物理明细存储合同

日期：2026-09-05。结构版本 `sql2014_0028`。接续 `0027` 的阶段运行字段整改，替代此前“测量明细仍共享”的当前设计说明；原完成报告仍作为历史记录保留。

## 物理结构与业务身份

| 对象 | 类型 | 职责 |
|---|---|---|
| test.cp_die | 物理表 | CP 芯粒结果；保留 Lot/Wafer 对应 Run、X/Y、来源序号、Bin、状态和原始证据 |
| test.ft_device | 物理表 | FT 器件结果；保留 NUM 对应的 unit_sequence、来源 Run、Site、厂商器件号、Bin 和状态；没有 Wafer/X/Y 列 |
| test.cp_measurement | 物理表 | CP 数值、原始文本、缺失/异常状态、来源列号；只允许关联 CP 芯粒 |
| test.ft_measurement | 物理表 | FT 数值和状态；只允许关联 FT 器件 |
| test.unit_identity | ID 登记表 | 仅 unit_id、test_stage，保证两阶段全局 ID 不冲突，供 Bin 评价和追溯外键使用 |
| test.measurement_identity | ID 登记表 | 仅 measurement_id、test_stage，供原测量评价外键使用；不存数值 |
| test.unit_result / test.measurement | 兼容查询视图 | UNION ALL 读取两阶段物理表，保持原列名和 ID；没有第二份活动事实 |
| test.*_legacy_0027 | 只读迁移快照 | 保存切换前原始物理表；数据库触发器阻止 INSERT/UPDATE/DELETE；不参与活动视图 |

`test.test_run`、CP/FT run_detail、Source、Dataset、参数定义和规格仍共享各自管理框架。CP/FT 阶段视图已直接引用对应物理表。正式、个人数据边界及 API 的 Owner/数据域/版本授权不变。

`unit_id` 和 `measurement_id` 是内部关联 ID，不是业务 NUM 或 CP 坐标。所有历史 ID 保持原值，因此收藏、导出、规格评价、Bin 评价和追溯关系不需要改号。原表头中的创建时间、原始值、状态、缺失和空字符串也必须原样迁移。

## 写入与完整性

StageFactRepository 是三条现有 Writer 的物理写入入口。每批次由 SQL Server sequence 分配全局唯一 ID 范围，再在调用方同一事务中写入 ID 登记和对应物理表。禁止应用通过 MAX(id)+1 分配运行时 ID。回滚可以留下 sequence 间隙，不复用旧 ID。

- CP/FT 物理表均有固定阶段 CHECK，并以 `(id,test_stage)` 外键关联 ID 登记；器件表以 `(run_id,test_stage)` 外键关联 Run。
- 测量表直接外键关联对应阶段器件表，不能把 CP 测量写到 FT 器件上。
- 原 Bin/测量评价、跨阶段 trace 的外键改指向 ID 登记，仍使用 SQL Server 原生、受信任外键。
- 所有 Writer 的结构化运行字段写入和原子发布流程保留。任一步骤失败必须退出调用方事务，不提交部分结果。
- CP 的坐标重复校验沿用 Cleaner 输出合同。历史重复测试保留，不按坐标去重，不从重复记录推断 attempt_no。
- FT 明细表没有晶圆坐标；兼容视图为旧公共列返回 NULL。Source_ID、制造批次等仍从 ft_run_detail 读取，不能把 NUM 当成跨文件全局身份。

兼容视图拒绝旧 INSERT/UPDATE 路径，防止继续写回共享表。受控清理仍可通过兼容视图 DELETE，触发器在同一语句事务中删除对应物理记录和 ID 登记；若评价或追溯仍引用该 ID，外键会阻止删除。旧迁移快照不随之删除。

## 迁移与存储

迁移要求停止写入、无 QUEUED/RUNNING Job、无 STAGED finalize intent，保留 NEEDS_INPUT 任务。仅支持已建立合同的 CP/FT；其他阶段或 FT 坐标证据须先解决，不能静默丢弃。

迁移步骤：服务器容量检查 → 创建 ID 登记与阶段表 → 按已有阶段复制 → 创建阶段索引 → 评价/追溯外键切换 → 原表只读快照及兼容视图 → 原数据逐行比对 → 刷新所有活动视图绑定 → 提交版本。步骤在同一迁移事务中；不一致时回滚。

容量检查查询 **SQL Server 自身卷** 的剩余空间和数据库内部空闲空间。约三倍源明细保留页加 512 MiB 是迁移工作空间估算，不是容量保证；应额外评估其他数据库、tempdb、日志、备份和并发工作负载。原表快照和 ID 登记都会增加存储量，不能宣称本次节省磁盘。

当前测试服务器曾在 C 盘出现空间不足，第一次迁移已完整回滚。后续为这个测试库在 SQL Server 的 `D:\TMS_Data` 增加独立数据/日志文件，并关闭原 C 盘文件的自动增长；本次失败事务留下的空闲日志空间已回收。此路径是服务器路径，不能当作用户电脑的 D 盘。其他部署环境应按其实际容量配置，不在 Alembic 中硬编码磁盘路径。

## 核验与回退

迁移逐行比较全部原始列：文本以二进制比较区分大小写及尾部空格，浮点值比较存储位模式，NULL 单独处理；同时检查数量与 ID。另行按 Run、Dataset Version、Pass 数量及测量数量对账，验证所有外键受信任，活动视图没有引用旧快照。

开发库验证入口是 `scripts/g0/verify_physical_facts.py`。升级前用 `--before` 生成基线，升级后用 `--baseline <JSON>` 复核；`--probe-writes` 验证 CP/FT 并发分配、兼容查询与删除、跨阶段拒绝和快照只读，探测事务最终回滚。新增真实导入后不能再用旧基线要求总数量完全不变，应区分新增版本。

回退使用验证过的备份和匹配代码，或做前向修复；不能切回只包含旧数据的快照继续写入，否则会丢失切换后的新增事实。正式恢复前需要处理新增数据，并根据 FILELISTONLY 为所有逻辑数据/日志文件逐项指定 MOVE。现有恢复脚本遍历全部文件，不应假定只有一个 MDF 和一个 LDF。

历史快照清理是后续受控维护，不在本次迁移中自动删除。生产环境上线、完整恢复演练和大容量性能上限须独立验收。
