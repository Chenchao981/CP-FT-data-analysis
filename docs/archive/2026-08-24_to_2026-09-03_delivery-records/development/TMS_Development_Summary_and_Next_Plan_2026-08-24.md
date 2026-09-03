# TMS Route A 开发总结与后续规划（2026-08-24）

## Executive Summary

- **当前结论：CP 已支持华虹、Jetech、立昂微和国宇 FRD 的真实源数据进入后台清洗、正式入库、版本发布和分析链路，但还不能称为整个 TMS 已完成。** A0/A1 核心底座已经形成，A2 CP 是可运行的首条纵向链路；A3 日月新 FT、A4 通用历史分析、A5 下载/删除闭环和 A6 生产发布尚未完成。
- **做得最好的地方是技术路线已经收敛，并且用真实数据发现和纠正了业务语义问题。** CP 与 FT 保持两个独立 Cleaner，工程/量产 × CP/FT 四个入口直接确定数据类型；明细只写入 `test.*`；多批次沿用第一批次 Spec；`CONT` 已按业务确认从参数、Spec 和 Measurement 中排除。
- **当前最大的功能缺口不是前端体积，而是 FT 的三个 Cleaner 输出尚未完成结构化入库，以及 A5 的导出/删除/TTL 未完成。** 这些问题应按功能闭环顺序处理，前端拆包继续后置。
- **服务器文件/目录路径入口已经补齐，下一阶段直接进入 A3 日月新 FT。** CP/FT Cleaner 都继续作为独立 Python CLI 程序运行，系统只负责传入文件或目录路径、读取输出并写入数据库；不重新讨论已冻结的 CP/FT 合并、数据库安全或前端拆包。

## 一、当前真实状态

### 1. 阶段状态不是“全部完成”，而是“一条 CP 链路已经跑通”

| 阶段 | 当前判断 | 已有成果 | 尚未达到原计划的地方 |
|---|---|---|---|
| A0 基线收敛 | 核心完成、正式门禁部分完成 | 真实盘点 Route B；冻结当前 CP/FT 实际输出合同；明确 Route A 和四入口规则 | FTP/Storage Adapter 未完成；BR-01～BR-10 的正式验收映射和签字未完成 |
| A1 Schema/队列/运行合同 | 核心完成 | `sql2014_0010/0011`；Cleaner Release；SQL Job Queue；租约/心跳/恢复；Owner/Admin 查询边界；异步上传；Worker在Windows Server 2019后台常驻运行 | 监控、自动重试操作界面和 Artifact TTL 清理未完成 |
| A2 CP | 可运行、四家公司现有格式已通过 | 华虹、Jetech、立昂微、国宇 FRD 已复用原 Python Cleaner；服务器文件/目录路径入口、CSV Adapter、Canonical、Dataset Current 和结果页已跑通 | 缺 Product/Lot 能力提示和完整 Statistics 快照仍可后续补齐；`立昂微-管芯数`保持独立低频功能，不进入常规 Die 明细链路 |
| A3 日月新 FT | 未开始正式结构化入库 | FT Cleaner Release、上传入口和调用合同已有 | 需要根据 Cleaner 的三个业务输出设计结构化表和字段，完成 FT Output Adapter、Canonical、Spec/Bin/参数映射及查询图表 |
| A4 历史查询与通用图表 | CP 局部完成 | Dataset/Version、Lot/Wafer、Yield、Bin、参数趋势、Pareto、Bin Map、Wafer Map 已可用 | 多任务/多 Dataset 通用筛选、服务端分页、BoxPlot/Histogram/Correlation、统一 Filter Context 未完整实现 |
| A5 下载/重清洗/删除 | 部分完成 | 重新处理已异步化；成功后生成新 Dataset Version，失败事务回滚，旧 Current 可保留 | `EXPORT_LATEST`、授权临时下载、TTL、Owner/Admin 物理删除、完整故障注入未完成 |
| A6 生产硬化 | 未开始 | 前后端开发服务可运行，生产构建已通过；Worker已在Windows Server 2019后台运行 | 服务器部署状态核验、安装/升级包、备份恢复、并发压测、监控、UAT、用户手册和正式发布未完成 |
| A7 新厂家接入 | 已有可复用案例 | Jetech、立昂微、国宇 FRD 已按现有输出合同接入并完成真实 SQL 全链路验证 | 后续新厂家仍需按其固定格式增加 Adapter 和真实样本验收 |

### 2. 已冻结的业务决定继续有效

1. CP 与 FT 保持两个独立程序，不做强制统一。
2. 前端保持工程-CP、工程-FT、量产-CP、量产-FT 四个入口；入口直接确定业务域和数据类型。
3. 多批次比较沿用第一批次 Spec；业务端只把相同 Spec 批次放在一起比较。
4. 隔离内网数据库安全不再作为当前开发讨论项。
5. 前端体积优化放在核心功能完成之后。

## 二、已经完成的工作

### 1. 架构和数据事实源已经收敛

- 删除了无数据、无外部依赖的 Route B 明细表；`analysis.saved_analysis` 作为分析配置保留。
- 正式测试明细只进入 `test.test_run`、`test.unit_result`、`test.measurement`。
- Dataset Version 负责 Current/Superseded；重新处理成功后发布新版本，旧版本保留历史。
- 原始文件、SHA256、Cleaner Release、Parser、Processing Run、Artifact、Dataset Version 和 Canonical 明细已经建立可追溯关系。

### 2. 上传和 Cleaner 已从同步请求改为后台任务

- 上传 API 保存文件、登记批次和 Job 后立即返回 `QUEUED`。
- SQL Server Job Queue 支持幂等键、租约、心跳、尝试次数和过期恢复。
- Worker 只领取已经注册的任务类型，并根据 Cleaner Release 读取运行时、入口、输出合同、超时和体积限制。
- Cleaner 包执行前校验 SHA256，避免实际程序与数据库登记版本不一致。
- “重新处理”已复用首次上传的同一条 Worker/Writer 链路，不再走旧的同步分支。

### 3. 华虹 CP 已形成真实纵向链路

- `CP_CSV_TRIPLET_V1` 严格读取 cleaned、yield、spec 三类 CSV。
- cleaned 与 yield 按 Lot/Wafer 对账 Total、Pass 和 Bin=1；不一致则整批失败。
- Lot、Wafer、Die、Bin、X/Y、参数值和第一批次 Spec 已写入 Canonical。
- 结果摘要关联 Dataset ID/Version，前端可从结果页直接进入对应数据分析。
- 分析页已验证 Yield、Bin Pareto、参数趋势、Bin Map 和 Wafer Map。

### 4. 真实样本验证扩大了格式覆盖

测试源目录共有 455 个文件、63,311,689 Bytes，包括 92 个 ZIP、1 个 7z 和 362 个 TXT。华虹数据类型和格式固定，本轮四类真实样本已经覆盖现有输入类型，可作为华虹格式验收结论：

| 样本 | Product/Lot | Wafer | Die | 参数数 | Pass | 结果 |
|---|---|---:|---:|---:|---:|---|
| 7z | NCETEN30CAC / FA5X-2565 | 25 | 3,875 | 13 | 3,775 | Cleaner + Canonical 对账通过 |
| ZIP `@202` | NCEVTG120EB60DB / FA4Z-8751 | 12 | 7,356 | 17 | 5,226 | 已真实入库 Dataset 11 Version 1 |
| ZIP `@203` | NCEVTG120EB60DB / FA59-8531 | 13 | 1,950 | 17 | 1,855 | Cleaner + Canonical 对账通过 |
| 原始 TXT 目录 | NCETG65EV30DA / 2 Lot | 25 | 4,000 | 17 | 3,718 | 保留目录身份时对账通过 |

同一 CP 页面已新增 Jetech、立昂微和国宇 FRD 选择，并使用服务器源路径完成真实清洗、Canonical 入库和结果查询：

| 厂家 | 真实源数据 | Lot | Wafer | Die | 参数数 | Pass | 结果 |
|---|---|---|---:|---:|---:|---:|---|
| Jetech | `jetech\2025-04\C146808.02` | C146808.02 | 1 | 2,581 | 22 | 2,393 | Batch 28 / Dataset 13，PROCESSED |
| 立昂微 | `立昂微\F25191360\F25191360_1.xlsx` | F25191360 | 1 | 682 | 10 | 675 | Batch 29 / Dataset 14，PROCESSED |
| 国宇 FRD | `国宇FRD\25B103\EDS\01#-759.xls` | `NULL` | 1 | 3,266 | 7 | 3,226 | Batch 30 / Dataset 15，PROCESSED |

国宇 FRD 源数据只有片号、没有业务批次号。系统允许其正常清洗，并在数据库中保留 `lot_id=NULL`，不使用目录名伪造批次。`立昂微-管芯数`属于独立、低频的晶圆管芯数功能，现有工具可用，本次没有将它混入常规 CP Die 明细流程。

### 5. `CONT` 业务语义已经纠正

- 业务确认 `CONT` 是计数符号，不是参数。
- Writer 在 cleaned 和 Spec 两边明确排除 `CONT`，其他未知多余列仍失败关闭。
- 华虹严格 Parser 和旧 Canonical Writer 同步排除 `CONT`，同时保留真实参数的原始列号。
- Dataset 9 Version 1（54,250 Measurements）已标记为 Superseded；Version 2 成为 Current，包含 3,875 Units、13 个参数和 50,375 Measurements，`CONT_present=False`。

## 三、完成得好的地方

### 1. 没有把 CP 和 FT 强行做成一个 Cleaner

任务框架、版本、队列和权限可以共用，但 CP/FT Parser、Output Adapter 和明细语义保持独立。这既符合业务事实，也避免为了“统一”破坏已经成熟的程序。

### 2. 用真实数据而不是合成样例决定适配规则

真实 `@202/@203` 样本发现了 cleaned 中 Wafer `02` 与 yield 中 `2` 的差异。Canonical 使用标准值 `2` 做业务对账，同时在元数据中保留原始 `02`。真实样本还发现 `CONT` 的业务语义问题，并通过新 Dataset Version 完成纠正。

### 3. 对未知或矛盾数据保持失败关闭

参数列、Lot/Wafer、坐标、Total、Pass、Spec、Cleaner 输出角色不一致时不会生成 Current 数据。Job 34 在 Parser 默认版本约束冲突时失败并完整回滚，旧 Dataset 仍可用；修复后 Job 35 成功发布 Version 2。

### 4. Current 切换和历史保留逻辑已经发挥作用

错误版本没有直接覆盖历史明细。Dataset 9 同时保留 Version 1 和纠正后的 Version 2，只有 Version 2 为 Current，证明版本边界能够支持重处理和审计。

### 5. 前端功能围绕业务路径，而不是围绕技术对象

用户从工程/量产 CP/FT 入口上传，结果页看到 Product、Lot、Wafer、Die、Yield，并可直接进入分析，不需要先理解 Processing Run、Parser Profile 或 Dataset Version 等后台概念。

### 6. 测试和文档已经开始形成可持续基线

- 后端 Unit Tests：83 passed。
- 前端 Tests：13 passed。
- 前端 Production Build：PASS。
- Route A Schema、Cleaner Registry、Initial Worker、租约恢复：PASS。
- 关键里程碑有独立完成报告，代码变更有本地 Git 提交。

### 7. 现有 Python Cleaner 的复用方式正确

系统没有重写 CP 清洗逻辑。`ExistingCleanerRunner` 使用独立 Python 子进程执行既有 Cleaner，并把输入文件或目录路径及输出目录传给 CLI。对于原始 TXT，只要系统提交服务器可访问的源目录路径，Cleaner 就能保留原有 Product/Lot 目录语义；因此应增加路径型任务入口，而不是把 TXT 扁平上传后再推断目录。

### 8. 三个 Cleaner 输出可以直接映射为系统数据库结构

GUI 工具时代的三个输出文件是数据合同，不要求 TMS 继续以三个 Excel/CSV 文件作为最终使用形式。系统可以保持相同清洗逻辑，将 cleaned 明细映射到 Run/Unit/Measurement，将 yield 映射到批次、晶圆和 Bin 汇总，将 spec 映射到 Spec Set/Spec Item/Test Item；文件仍作为可追溯 Artifact 保存，业务查询和分析使用结构化数据库。

### 9. CP 多厂家没有重写成熟清洗逻辑

Jetech、立昂微和国宇 FRD 都由系统后台把服务器文件或目录路径传给现有 `F:\cp_data_ansys` Python Cleaner，再读取 cleaned、yield、spec 输出。系统新增的是厂家注册、输入格式边界和数据库适配，不复制或改写已经能用的 GUI 清洗算法。

## 四、完成得不够好的地方

### 1. 先前报告一度把 A2 写得过于完整

早期状态报告把“A2 首条链路跑通”接近写成“A2 已完成”，但按原计划退出条件，缺失能力弹窗、完整 Statistics 快照、全部 BR-01～BR-05 自动化、正式 Golden Manifest 和业务验收仍未完成。本报告将状态改为“可运行、未完全验收”。

### 2. 文档和数据库事实发生过漂移

原 A2 报告曾把 Dataset 9 Version 1 写成 13 参数、50,375 Measurements；数据库事实是 Version 1 有 54,250 Measurements，纠正后的数据属于 Version 2。报告必须随版本切换同步更新，不能只修改数字而不修改 Version/Spec Set。

### 3. 任务耗时没有分阶段记录，原报告归因不准确

- Job 33 总用时 428 秒，处理 7,356 Units、125,052 Measurements。
- Job 35 总用时 184 秒，处理 3,875 Units、50,375 Measurements。

这些数字包含 Python Cleaner、输出文件读取、数据校验、Canonical 写入和 Dataset 发布全过程，不能据此判断“清洗太慢”，也不能直接判断“Canonical 写入太慢”。当前真正缺少的是分阶段计时。功能优先阶段不据此启动性能改造；后续只在实际使用出现等待问题时，再根据分段数据定位 Cleaner、文件解析或数据库写入。

### 4. Worker 当前没有已知功能问题

业务确认 Worker 已在 Windows Server 2019 后台持续运行。代码中的 Worker 会持续轮询 SQL Job Queue、领取任务并执行 Python Cleaner；只要服务器实际部署会在开机后启动该独立 Worker 进程，当前不存在任务因开发命令窗口关闭而中断的问题。后续只需在正式发布核验中确认 Worker 进程状态和服务器重启后的自动恢复，不再把它列为“完成得不好”的问题。

### 5. 日志当前满足 AI 诊断要求

系统已保存任务状态、错误代码、错误消息和 Cleaner 输出尾部。只要 AI 能读取原始日志并结合结构化状态定位问题，就不要求为人工阅读单独美化历史 Cleaner 控制台文字；后续只需确保原始信息不丢失。

### 6. 真正尚未完成的是 FT 三类输出的结构化映射

问题不在于 Cleaner 输出是 Excel 还是 CSV，而在于日月新 FT 的 cleaned、统计/汇总和 spec 数据尚未全部转换为系统表。应基于三个输出的数据字段设计 FT 的 Run、Unit、Measurement、Test Item、Spec 和汇总结构，保持原 GUI 工具的业务逻辑不变。

## 五、下一阶段规划

### P0：路径型任务入口和 CP 多厂家接入（已完成）

1. 前端工程/量产 CP 均可选择华虹、Jetech、立昂微、国宇 FRD。
2. 用户可上传文件，也可提交服务器可访问的文件或目录路径，两者二选一。
3. Worker 将路径传给现有 Python CLI Cleaner，输出写入任务独立目录。
4. 已保留源路径、输入 SHA256、Cleaner Release 和三个输出 Artifact 的追溯关系。
5. 分阶段计时、缺字段能力提示和 Statistics 快照留作完善项，不阻塞四家公司固定格式投入使用。

### P1：完成 A3 日月新 FT 纵向链路

1. 用真实日月新 FT 样本确认三个 Cleaner 输出文件及字段。
2. 基于三个输出设计 FT 的结构化数据库表和字段；文件作为输入合同和 Artifact，不作为系统查询主数据源。
3. 实现独立 FT Output Adapter，不修改 CP Cleaner 或 CP Writer。
4. 映射 Product、Lot、Unit、PASS/FAIL、Bin、参数、测试条件和 Spec。
5. 写入同一套任务/Dataset/Canonical 框架，但保留 FT 独立字段语义。
6. 完成 FT 结果页、Yield/Bin、参数分布和 Scatter。
7. 验证工程-FT与量产-FT两个入口、Owner 隔离和缺字段提示。

### P2：扩展 A4 历史查询与通用分析

1. 增加跨任务、跨 Dataset、Product/Lot/Wafer/Bin/参数的服务端筛选。
2. 明细查询全部服务端分页、排序和聚合，浏览器不加载全量 Measurement。
3. 补齐 BoxPlot、Histogram、Scatter、Correlation 和统一 Filter Context。
4. 多批次比较继续使用第一批次 Spec，并在页面明确提示“仅选择相同 Spec 批次”。

### P3：完成 A5 下载、重清洗和删除闭环

1. `EXPORT_LATEST` 临时调用最新 Cleaner，只生成下载文件，不改 Canonical。
2. 临时 Artifact 授权、校验、下载和 TTL 清理。
3. 重清洗故障注入：Cleaner 失败、导入中断、发布前失败时旧 Current 始终可用。
4. 普通用户删除本人任务，管理员删除任意任务；同 Lot、同 SHA、不同 Owner 互不影响。

### P4：A6 生产硬化与发布

1. 核验 Windows Server 2019 上现有 Worker 后台进程、开机启动和异常恢复；保持现有可用部署方式，不要求为了形式重新改造。
2. 使用真实规模验证 1～2 个 Worker、查询并发和数据库执行计划。
3. 完成备份恢复、Worker 重启、源文件不可用和数据库中断演练。
4. 形成 Windows Server 安装/升级/回滚包、运维手册和用户手册。
5. 华虹 CP + 日月新 FT 完成业务 UAT。
6. 核心功能完成后再进行前端按页面加载、拆包和体积优化。

## 六、下一步验收顺序

建议严格按以下顺序推进，避免同时铺开过多半成品：

1. 将已通过的华虹、Jetech、立昂微、国宇 FRD 固定格式样本保留为回归测试，稳定 A2 使用流程。
2. 立即进入日月新 FT 三个输出的表结构设计、Adapter 和真实样本入库。
3. CP/FT 两条链路都稳定后，再统一扩展历史筛选和图表。
4. 最后完成导出、删除、运维发布和前端体积优化。

## 七、需要业务继续确认的问题

1. 日月新 FT 用于三个输出字段对账的源数据目录和批准样本是哪一批？

## 八、报告口径与限制

- “已完成”只使用当前代码、SQL Server 开发库结果、真实 Cleaner 产物、自动测试和实际界面验证作为证据。
- 华虹现有数据类型和固定格式已经由真实样本覆盖；后续样本继续作为回归验证，不再设置“92个ZIP全量通过”的额外门槛。
- 任务总耗时来自单 Worker、开发数据库和本机环境，未做分阶段计时，不能据此归因 Cleaner 或数据库性能。
- 本报告不重新讨论已冻结的数据库安全、CP/FT 合并和前端体积优先级。
