# CP/FT 阶段合同与扩展指南

日期：2026-09-05。继承 9 月 3 日路线图、9 月 4 日直接使用正式工具包决定，以及最新 CP/FT 分模型要求。替代旧交接中继续建设 analysis.* 明细链路的建议。

## 1. 当前实现边界

| 层 | 实现 | 职责 |
|---|---|---|
| 阶段模型 | `backend/app/domain/cp_data.py`、`ft_data.py` | CP/FT 独立记录与规格类型，不依赖 SQL、厂商包或前端 |
| 能力目录 | `backend/app/domain/cleaner_capabilities.py` | 阶段、厂家、用途、格式方法、输入输出合同 |
| 工具调用 | `existing_cleaner_runner.py`、`quick_tool_runner.py` | 校验正式包、调用成熟工具，禁止复制厂商算法 |
| 标准结果转换 | `cp_csv_triplet_writer.py`、`ft_xlsx_scatter_writer.py` | 分别验证各阶段输出并转换入库 |
| 生命周期 | Dataset、Job、Source、个人 Session | 统一任务框架；正式与个人身份、保留周期分开 |
| 展示 | `AnalysisResultFrame`、`AnalysisEvidence`、`PatResultView` | 统一结果归属与证据，不推断未知数量 |

**数据库物理明细已通过 `sql2014_0028` 拆分为 cp_die、ft_device、cp_measurement、ft_measurement。** `sql2014_0027` 的 CP/FT 独立运行字段继续使用；原公共名称成为兼容查询视图，三条 Writer 通过 StageFactRepository 写入对应物理表。详见 [数据库阶段字段合同](NCE_PYMS_Database_Stage_Fields_2026-09-05.md) 和 [物理存储合同](NCE_PYMS_Physical_Stage_Storage_2026-09-05.md)。旧 analysis.* 不作为第二条正式事实链。

## 2. 身份与参数语义

- CP 清洗快照中的 Die 身份为 `(Lot, Wafer, X, Y)`；Seq 是来源序号，改变 Seq 不能绕过重复坐标检查。保留旧 logical_key 表示以兼容历史追溯。
- FT 身份为 `(Lot, Source_ID, NUM)`；不能从测试序号推导晶圆坐标，也不能把不同源文件相同 NUM 合并。
- 电基额外制造批次、周记/批次、测试标签保留在来源身份元数据中；不把它们任意改名为 CP 晶圆 Lot。
- 参数必须结合名称、单位、测试条件和适用规格验证；不能仅凭显示名称合并跨批次参数。
- CP/FtSpecItem 保存标准数值、原始规格文本及测试条件；FT 额外保存 Bias1/Bias2。这里的原始文本是 **Cleaner 标准输出所保留的文本**，不宣称所有厂家都已经输出原始仪器单位、原始列名或结构化 Bias。
- 某厂家没有提供的源参数名、原始单位、测试编号、Site、制造批次等保留未知。需要补充时由该厂家输出合同提供，再扩展阶段模型；TMS 不反向猜测显示名。
- 源文件测试限不自动等于批准产品规格；没有可确认 Bin/PASS/FAIL 时 Yield 保持未知。

## 3. 新厂家及新格式怎么扩展

1. 在 CP 或 FT 原工具中维护该厂家专用识别/解析方法，生成正式包。
2. 在能力目录登记准确方法及输出合同。检测与解析合一的旧工具，其 detector_entrypoint 指向执行内容检查的正式入口；此目录不会动态 import 任意名称。
3. 输出合同不变时复用现有转换器；合同变化时增加新转换器并保留旧合同。
4. 正式上传必须使用精确用途合同选包，不能仅用“阶段+厂家取最新”。个人 PAT 包不能充当正式清洗包。
5. 固定源样本的文件清单、规格语义、行数、缺失数和统计值。正确失败也是验收：未知格式、坐标重复、条件冲突不得放行。
6. TMS 登记正式包并核对 SHA；Git 管发布历史。既有 Dataset 不自动重算。

当前正式入口六个厂家全部进入能力目录：华虹、积塔、立昂微、日月新、日月光、电基。个人工具仍按其专门合同调度，能力目录不据此宣称所有格式都已支持个人电脑执行。

## 4. 已完成的物理分表与维护要求

CP Die/Measurement 与 FT Device/Measurement 已分表，共享 Source、Dataset、参数定义和任务。迁移及维护遵循以下要求：

1. 基于现有正式样本先核对阶段身份、重复/重测与 FT 元数据覆盖率；清洗快照与重测历史必须分清。
2. 全量回填分阶段表，原 ID 保持原值；窄 ID 登记表承接评价与追溯外键，旧事实快照只读。
3. 每个版本对账 Unit、Measurement、状态数、Pass、Lot/Source 分组、规格语义及数值摘要。保留 ID 对照，避免收藏、下载和证据链接失效。
4. 查询通过阶段 Repository 切换；前端接口及原来权限谓词保持一致。CP/FT 混合查询不得进入同阶段统计。
5. 新旧查询对账、并发读取和性能通过后才切换写入。切换期间单一事实写入目标，禁止两套统计成为事实来源。
6. 回退必须停止写入，使用备份及匹配代码或前向修复，并处理切换后的新增事实；不能直接切回不含新增事实的旧表。历史快照清理另行安排。

阶段合同、运行字段和物理事实切换均已完成。实际迁移保留全部 1,002,163 条器件记录、18,353,936 条测量记录及其 ID，提交前逐行比较原始列；后续真实样本导入形成新增测试版本。结果与容量限制见 [物理分表完成报告](../development/NCE_PYMS_Physical_Stage_Storage_Completion_Report_2026-09-05.md)。

## 5. 个人电脑与服务器

- “同机/共享路径”指运行后端的电脑可见的路径。
- “个人电脑”使用文件所在电脑的本地运行程序，仅传结果。当前已验证杰群统一 CSV PAT。
- 安装包生成入口：`scripts/release/build_local_analysis_bundle.py`；显式指定真实网页 Origin、正式 FT 包和输出 ZIP。生成包内不含账号、配对令牌或源数据。
- 第二台电脑仍须核对 Python 依赖、网页 Origin、已登记 SHA，并从选择目录走到服务器个人历史/下载验收。单机隔离目录测试不能替代该验收。

## 6. 验证与暂缓项

回归默认执行 `python -m pytest tests/unit local_agent/tests -q`、前端 `npm test` 和 `npm run build`；实际解释器使用仓库环境。真实性能脚本 `scripts/g0/verify_direct_quick_pat_e2e.py` 输出源清单、行数、耗时、子进程采样内存、临时磁盘峰值和残留情况；外部工具运行使用完整 Anaconda 环境。

FTP 定时采集、SAP、异常工单、AI 保持暂缓；业务规格和规则必须有批准依据，不用默认值补齐。
