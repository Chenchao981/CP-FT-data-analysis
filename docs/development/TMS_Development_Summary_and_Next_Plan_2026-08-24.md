# TMS Route A 开发总结与后续规划（2026-08-24）

## Executive Summary

- **当前结论：系统已经从“有上传页面和 Cleaner”推进到“华虹 CP 可以真实上传、后台清洗、正式入库、版本发布并进入分析页面”，但还不能称为整个 TMS 已完成。** A0/A1 核心底座已经形成，A2 华虹 CP 是可运行的首条纵向链路；A3 日月新 FT、A4 通用历史分析、A5 下载/删除闭环和 A6 生产发布尚未完成。
- **做得最好的地方是技术路线已经收敛，并且用真实数据发现和纠正了业务语义问题。** CP 与 FT 保持两个独立 Cleaner，工程/量产 × CP/FT 四个入口直接确定数据类型；明细只写入 `test.*`；多批次沿用第一批次 Spec；`CONT` 已按业务确认从参数、Spec 和 Measurement 中排除。
- **当前最大的功能缺口不是前端体积，而是 FT 尚未正式入库、原始 TXT 的 Web 上传不能保留 Product/Lot 目录身份、华虹大批量 Measurement 写入过慢，以及 A5 的导出/删除/TTL 未完成。** 这些问题应按功能闭环顺序处理，前端拆包继续后置。
- **建议下一阶段先关闭 A2 的真实使用缺口，再立即进入 A3 日月新 FT。** A2 关闭范围只包含原始 TXT 目录上传、Golden 自动验收、写入性能、能力提示和统计快照；不重新讨论已冻结的 CP/FT 合并、数据库安全或前端拆包。

## 一、当前真实状态

### 1. 阶段状态不是“全部完成”，而是“一条 CP 链路已经跑通”

| 阶段 | 当前判断 | 已有成果 | 尚未达到原计划的地方 |
|---|---|---|---|
| A0 基线收敛 | 核心完成、正式门禁部分完成 | 真实盘点 Route B；冻结当前 CP/FT 实际输出合同；明确 Route A 和四入口规则 | FTP/Storage Adapter 未完成；BR-01～BR-10 的正式验收映射和签字未完成；实际输出仍不是原规划中的三个 XLSX |
| A1 Schema/队列/运行合同 | 核心完成 | `sql2014_0010/0011`；Cleaner Release；SQL Job Queue；租约/心跳/恢复；Owner/Admin 查询边界；异步上传 | Worker 仍以开发脚本方式运行，尚未成为 Windows 服务；监控、自动重试操作界面和 Artifact TTL 清理未完成 |
| A2 华虹 CP | 可运行、未完全验收 | Cleaner → CSV Adapter → Canonical → Dataset Current → 结果页 → 分析图表已跑通；真实 ZIP、7z、TXT 目录样本对账通过 | 原始 TXT 的 Web 多文件上传会丢失目录身份；缺 Product/Lot 能力提示弹窗未完成；Statistics 仍主要是摘要而非完整快照；Golden 尚未覆盖全部样本库；性能未达生产水平 |
| A3 日月新 FT | 未开始正式结构化入库 | FT Cleaner Release、上传入口和调用合同已有 | FT Output Adapter、Canonical、Spec/Bin/参数映射、查询图表和 Golden 对账未完成 |
| A4 历史查询与通用图表 | CP 局部完成 | Dataset/Version、Lot/Wafer、Yield、Bin、参数趋势、Pareto、Bin Map、Wafer Map 已可用 | 多任务/多 Dataset 通用筛选、服务端分页、BoxPlot/Histogram/Correlation、统一 Filter Context 未完整实现 |
| A5 下载/重清洗/删除 | 部分完成 | 重新处理已异步化；成功后生成新 Dataset Version，失败事务回滚，旧 Current 可保留 | `EXPORT_LATEST`、授权临时下载、TTL、Owner/Admin 物理删除、完整故障注入未完成 |
| A6 生产硬化 | 未开始 | 前后端开发服务可运行，生产构建已通过 | Windows 服务/安装包、备份恢复、并发压测、监控、UAT、用户手册和正式发布未完成 |
| A7 新厂家接入 | 未开始 | Adapter/Release 框架已具备扩展方向 | 尚无新增厂家完整 Golden 接入案例 |

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

测试源目录共有 455 个文件、63,311,689 Bytes，包括 92 个 ZIP、1 个 7z 和 362 个 TXT。本轮选取四类代表样本：

| 样本 | Product/Lot | Wafer | Die | 参数数 | Pass | 结果 |
|---|---|---:|---:|---:|---:|---|
| 7z | NCETEN30CAC / FA5X-2565 | 25 | 3,875 | 13 | 3,775 | Cleaner + Canonical 对账通过 |
| ZIP `@202` | NCEVTG120EB60DB / FA4Z-8751 | 12 | 7,356 | 17 | 5,226 | 已真实入库 Dataset 11 Version 1 |
| ZIP `@203` | NCEVTG120EB60DB / FA59-8531 | 13 | 1,950 | 17 | 1,855 | Cleaner + Canonical 对账通过 |
| 原始 TXT 目录 | NCETG65EV30DA / 2 Lot | 25 | 4,000 | 17 | 3,718 | 保留目录身份时对账通过 |

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

- 后端 Unit Tests：75 passed。
- 前端 Tests：13 passed。
- 前端 Production Build：PASS。
- Route A Schema、Cleaner Registry、Initial Worker、租约恢复：PASS。
- 关键里程碑有独立完成报告，代码变更有本地 Git 提交。

## 四、完成得不够好的地方

### 1. 先前报告一度把 A2 写得过于完整

早期状态报告把“A2 首条链路跑通”接近写成“A2 已完成”，但按原计划退出条件，缺失能力弹窗、完整 Statistics 快照、全部 BR-01～BR-05 自动化、正式 Golden Manifest 和业务验收仍未完成。本报告将状态改为“可运行、未完全验收”。

### 2. 文档和数据库事实发生过漂移

原 A2 报告曾把 Dataset 9 Version 1 写成 13 参数、50,375 Measurements；数据库事实是 Version 1 有 54,250 Measurements，纠正后的数据属于 Version 2。报告必须随版本切换同步更新，不能只修改数字而不修改 Version/Spec Set。

### 3. 原始 TXT 的 Web 上传目前不是真正可用

现有上传界面允许选择多个 TXT，但普通浏览器多文件上传会丢失 Product/Lot 目录层级。Cleaner 可以处理保留目录身份的 TXT 目录，但不能从一组扁平 TXT 文件可靠推断 Product。当前应视为明确功能缺口，不能继续把“支持多个 TXT”作为已完成功能宣传。

### 4. Canonical 写入性能明显不足

- 7,356 Units、125,052 Measurements 的 Job 33 用时 428 秒，约 292 Measurements/秒。
- 3,875 Units、50,375 Measurements 的 Job 35 用时 184 秒，约 274 Measurements/秒。

当前 Writer 在一个长事务中批量插入，处理期间普通查询出现锁等待。功能能够完成，但真实批量使用前需要改为高效 Staging/Bulk Insert，并把 Current 切换缩短为短事务。

### 5. 代表样本通过不等于整个样本库通过

本轮覆盖了 7z、`@202`、`@203` 和原始 TXT 目录，但没有自动遍历验证全部 92 个 ZIP。当前证据说明“代表格式可运行”，不能说明“所有历史华虹文件 100% 通过”。

### 6. Worker 还不是可运维的后台服务

开发环境通过命令启动 Worker；尚无 Windows 服务安装、自动启动、健康监控、失败告警和管理员重试页面。Web 服务正常不代表 Worker 一定正在消费队列。

### 7. Cleaner 日志编码仍不理想

Cleaner 能成功运行，但部分历史程序输出按 GBK 产生，当前 UTF-8 捕获后的 stdout 尾部仍有乱码。错误代码和任务状态可用，但运维人员阅读详细 Cleaner 日志不够友好。

### 8. 输出合同与原规划仍不一致

原规划写“三个 XLSX”，当前实际合同是：

- 华虹 CP：cleaned/yield/spec CSV；
- 日月新 FT：cleaned XLSX + scatter data/spec/manifest。

当前按真实程序工作是正确选择，但规划文档仍需统一改成“按 Cleaner Release 的版本化输出合同”，不能继续把三个 XLSX 当作现状。

### 9. 远程代码尚未同步

本地提交 `fdf8be4` 和 `bcea4e9` 已完成，但 GitHub HTTPS 多次出现 `SSL_ERROR_SYSCALL`。远程分支仍停留在 `d055b07`，当前成果尚未形成远程备份或 PR。

## 五、下一阶段规划

### P0：关闭 A2 华虹 CP 的真实使用缺口

1. **原始 TXT 目录上传**：前端提供目录选择或明确只接收 ZIP/7z；后端保留相对目录，确保 Product/Lot 身份不丢失。
2. **Golden 自动验收**：建立样本清单、输入 SHA256、预期 Product/Lot/Wafer/Die/Bin/参数/Spec/Yield；先覆盖四类代表样本，再批量扫描 92 个 ZIP。
3. **高效 Canonical 写入**：使用 SQL Server Staging + Bulk Insert/`fast_executemany`；大数据写入在 Staging 完成，发布 Current 只做短事务校验和切换。
4. **缺失能力提示**：缺 Product、Lot、Wafer/X/Y、Spec 时明确告诉用户哪些分析不可用，并提供任务级补录入口。
5. **Statistics 快照**：将 Cleaner 的统计输出作为可追溯快照保存，并与数据库重算结果对账。
6. **日志可读性**：识别 Cleaner 控制台编码，保留原始日志文件和可读的结构化错误摘要。
7. **A2 正式验收**：BR-01～BR-05、图表与 SQL 对账、失败回滚和真实用户操作全部通过后，才把 A2 标记为完成。

### P1：完成 A3 日月新 FT 纵向链路

1. 用真实日月新 FT 样本冻结当前 Output Contract 和 Golden Manifest。
2. 实现独立 FT Output Adapter，不修改 CP Cleaner 或 CP Writer。
3. 映射 Product、Lot、Unit、PASS/FAIL、Bin、参数、测试条件和 Spec。
4. 写入同一套任务/Dataset/Canonical 框架，但保留 FT 独立字段语义。
5. 完成 FT 结果页、Yield/Bin、参数分布和 Scatter。
6. 验证工程-FT与量产-FT两个入口、Owner 隔离和缺字段提示。

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

1. Worker 安装为 Windows 服务，增加自动启动、健康检查、失败告警和管理员重试。
2. 使用真实规模验证 1～2 个 Worker、查询并发和数据库执行计划。
3. 完成备份恢复、Worker 重启、源文件不可用和数据库中断演练。
4. 形成 Windows Server 安装/升级/回滚包、运维手册和用户手册。
5. 华虹 CP + 日月新 FT 完成业务 UAT。
6. 核心功能完成后再进行前端按页面加载、拆包和体积优化。

## 六、下一步验收顺序

建议严格按以下顺序推进，避免同时铺开过多半成品：

1. 修复原始 TXT 目录身份和 Canonical 写入性能。
2. 将华虹代表样本变成可重复执行的 Golden Test，并关闭 A2 验收缺口。
3. 立即进入日月新 FT Adapter 和真实样本入库。
4. CP/FT 两条链路都稳定后，再统一扩展历史筛选和图表。
5. 最后完成导出、删除、运维发布和前端体积优化。

## 七、需要业务继续确认的问题

1. 原始 TXT 是否要求在 Web 中直接选择目录，还是业务端统一先压缩成 ZIP/7z 后上传？
2. 日月新 FT 用于 Golden 对账的源数据目录和批准样本是哪一批？
3. A2 正式验收是否以四类代表样本为首批门槛，还是要求 92 个 ZIP 全量通过后再进入 FT？

## 八、报告口径与限制

- “已完成”只使用当前代码、SQL Server 开发库结果、真实 Cleaner 产物、自动测试和实际界面验证作为证据。
- 当前样本验证是代表性覆盖，不是全部历史数据兼容性声明。
- 性能数据来自单 Worker、开发数据库和本机环境，只能作为当前瓶颈证据，不能直接当作生产容量承诺。
- 本报告不重新讨论已冻结的数据库安全、CP/FT 合并和前端体积优先级。
