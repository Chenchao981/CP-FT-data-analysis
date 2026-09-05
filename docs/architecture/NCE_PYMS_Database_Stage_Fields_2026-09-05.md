# NCE PYMS 数据库阶段字段合同

日期：2026-09-05；数据库版本：`sql2014_0027`。

后续更新：当前版本已升级到 `sql2014_0028`，CP/FT 器件与测量事实已物理分表。本文的运行字段字典继续有效；下文关于“共享测量物理表”和“尚未分表”的阶段性描述已由 [物理存储合同](NCE_PYMS_Physical_Stage_Storage_2026-09-05.md) 替代。

## 业务规则与结构选择

用户确认当前库为测试数据，重复记录由多次测试产生。本次采用“共享测量事实、独立 CP/FT 运行身份”的结构优化。它落实阶段专属字段与数据库约束，保留既有 Dataset、源文件、测量 ID 和评价外键。不是把测量值复制到两套表，也不是删除重复测试记录。

| 业务信息 | 存储 | 规则 |
|---|---|---|
| 产品、业务 Lot、阶段、设备、处理任务、测试时间 | `test.test_run` | 继续使用现有字段；业务 Lot 未知时不从制造批次推断 |
| CP 原始晶圆号、来源分组、原始测试 Lot 字符串 | `test.cp_run_detail` | 一次运行一条；只能关联 CP 运行 |
| FT 来源、制造批次和测试标签 | `test.ft_run_detail` | 一次运行一条；只能关联 FT 运行；不同运行允许相同 Source_ID |
| 本次 Cleaner 输出对应规格 | 两个详情表的 `source_spec_set_id` | 可空；引用必须同阶段；不等于自动批准产品规格 |
| Die/Device、测量、Bin/Spec 评价 | 既有 `test.unit_result / measurement` 及评价表 | 单一事实源；不重编号、不重算历史 |
| 个人分析结果 | 既有 `workspace.analysis_session`、结果制品 | 不复制到正式事实表，权限和保留期不变 |

这次不执行 9 月 5 日扩展指南中的全部物理明细分表。共享测量表与现有评价、追溯外键具有直接关系；本次先将有不同业务语义的运行身份结构化。四个阶段视图是查询合同，不是独立物理测量表。将来确需独立物理测量存储时，仍须迁移评价外键、逐版本对账并验证容量与性能。

## 字段字典

`run_id` 是两个详情表的主键。`test_stage` 有固定值 CHECK，并通过 `(run_id,test_stage)` 外键保证 CP/FT 不串用。`source_spec_set_id` 通过 `(spec_set_id,test_stage)` 外键保证不跨阶段绑定。

| 表 | 字段 | 类型 | 含义与来源 |
|---|---|---|---|
| cp_run_detail | raw_wafer_id | nvarchar(64), NULL | Cleaner 保留的原始晶圆号；不凭格式补前导零 |
| cp_run_detail | source_group | nvarchar(128), NULL | Cleaner 的输入分组；`SOURCE` 不自动成为业务 Lot |
| cp_run_detail | source_lot_run | nvarchar(128), NULL | 历史华虹 Parser 已明确记录的原始测试 Lot 字符串 |
| ft_run_detail | source_id | nvarchar(256), NOT NULL | 已明确的 FT Source_ID；不等于设备号或全库唯一编号 |
| ft_run_detail | source_file | nvarchar(1024), NULL | 已记录的来源文件名；历史缺失保持未知 |
| ft_run_detail | manufacturing_lot | nvarchar(128), NULL | 电基合同明确提供的制造批次；不覆盖业务 Lot |
| ft_run_detail | test_tag | nvarchar(128), NULL | 厂家测试标签 |
| ft_run_detail | test_file_name | nvarchar(128), NULL | 厂家 TestFileName；不与上传文件名混用 |
| ft_run_detail | source_segment | nvarchar(128), NULL | 原输出合同提供的来源段标识 |
| ft_run_detail | source_format | nvarchar(64), NULL | 已识别并登记的厂家格式 |
| ft_run_detail | metadata_lot | nvarchar(128), NULL | 厂家原始元数据 Lot；与业务 Lot、制造批次分别保存 |
| 两表 | source_spec_set_id | bigint, NULL | 原运行元数据中明确的 `spec_set_id`，不从最新规格回填 |

未知格式、冲突 Source_ID、超长字段、非文本身份和非法规格 ID 拒绝写入；缺失可选字段保持 NULL。历史 JSON 保留为来源证据快照，分析来源选择使用结构化 Source_ID。TMS 不反向解析厂家文件名以补全这些字段。

参数定义仍使用 `mdm.test_item_definition` 的名称、单位、原始上下限、测试条件及程序版本；规格仍使用 `mdm.spec_item`。本次不更改浮点精度、参数合并规则、Bias 或正式规格判定算法。

## 入库、查询与索引

- 三个现有 Writer 在同一入库事务中调用 `persist_stage_run_details`，详情写入失败时整个导入事务回滚。
- `test.v_cp_die / v_cp_measurement` 仅提供 CP 字段和记录；`test.v_ft_device / v_ft_measurement` 提供 FT 来源身份，不伪造晶圆坐标。
- 正式分析、参数关联、散点图和导出来源选择读取 FT 结构化 Source_ID。原有 Dataset Version、Owner、数据域授权条件保留；数据库视图本身不代替 API 权限检查。
- FT 增加 `(source_id,run_id)` 索引和非空制造批次索引。CP 的 Lot/Wafer 和坐标索引继续复用。此次未用基准测试证明提速倍数。
- 详情表允许随所属运行删除而级联清理，不能脱离运行独立存在；历史测量及评价表没有设置新的级联删除。

多次上传/重清洗按 Processing Run 和 Dataset Version 保留。业务重测使用已有运行/器件 attempt 字段；不根据重复坐标自动编造重测次数。新 CP 清洗快照的重复坐标仍按原合同拒绝。

## 升级与核验

先停止写入，确认没有 QUEUED/RUNNING Job 或 STAGED finalize intent，备份并验证备份，再执行 Alembic `upgrade head`。已有 NEEDS_INPUT 任务不自动取消；恢复后仍沿用原任务和补录合同。

迁移在事务中解码旧 JSON、校验长度/类型/冲突、建表并回填。不使用 SQL Server 2014 不支持的 JSON 函数，也不依赖未来运行时代码。遇到不兼容记录时回滚迁移，不跳过记录。

项目根目录中的复核入口：

```powershell
. ./.env.runtime.ps1
./.conda-env/python.exe scripts/g0/verify_stage_run_schema.py --output artifacts/runtime/stage-schema-check.json
```

升级前可以 `--before` 保存旧版本基线，升级后通过 `--baseline <基线JSON>` 核对各运行记录数量、状态和字段。`--probe-writes` 仅用于开发库，验证跨阶段拒绝和 Writer 回放，所有探测事务最终回滚。数值摘要采用 BINARY_CHECKSUM 聚合，只作变化检测，不称为密码学级逐值证明。

回退使用已校验备份及对应代码版本，或新的前向修复迁移；不提供删除详情表的自动降级。恢复备份前应另行处理备份后新增数据，不能直接覆盖。
