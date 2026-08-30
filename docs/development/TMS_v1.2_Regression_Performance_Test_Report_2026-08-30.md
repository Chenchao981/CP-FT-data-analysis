# TMS v1.2 回归与性能测试报告

- 日期：2026-08-30
- 对象：工程私有、量产共享、重复上传独立分析、Dataset-scoped Current
- 代码基线：`codex/auth-rbac-frontend`
- 数据库：`TMS_G0_DEV`
- SQL Server：`12.0.5000.0`
- Schema：`sql2014_0019`
- 总体结论：功能回归 PASS；现有数据覆盖的性能探针 PASS；完整性能结论为 COVERAGE SKIP

## 1. 测试原则

- 原始业务数据只读，E2E 使用随机、可识别、精确清理的夹具；
- 所有破坏性 SQL 都限定开发库名称和唯一 Schema head；
- 读取性能脚本只允许 SELECT 或只读 CTE，并比较前后业务表计数；
- 未具备真实数据覆盖的场景明确 SKIP，不用估算值代替实测；
- 本机结果不外推目标服务器和生产并发峰值。

## 2. 数据规模

| 项目 | 实测值 |
|---|---:|
| 数据文件 | 1,022.19 MB |
| 日志文件 | 565.44 MB |
| Source | 21 |
| Receipt | 66 |
| Import Batch | 23 |
| Processing Job | 38 |
| Dataset | 18 |
| Published Current Dataset Version | 10 |
| 量产 Current Dataset Version | 3（全部 FT） |
| Test Run | 139 |
| Unit Result | 291,127 |
| Measurement | 5,578,114 |

## 3. 自动化回归

| 测试 | 结果 | 说明 |
|---|---|---|
| Python 全量 | `520 passed, 1 skipped` | 39.81 s；4 个 openpyxl 弃用警告 |
| Python Skip 原因 | SKIP | 当前 Windows 账户不能创建目录符号链接；专项复核为 `36 passed, 1 skipped` |
| 前端全量 | `24 files / 125 tests passed` | 116.99 s；无失败 |
| TypeScript + Vite Build | PASS | 13,055 modules transformed |
| Ruff F/I | PASS | 所有本轮 Python 文件 |
| Ruff format | PASS | 所有本轮 Python 文件 |
| `git diff --check` | PASS | 仅现有 CRLF 转换提示 |
| PowerShell AST/合同 | PASS | 运行、维护、Migration、发布脚本 |

前端测试环境持续输出 jsdom 不实现伪元素 `getComputedStyle()` 的提示，不影响断言。Vite 对 EChart 约 1,120 KB、主包约 2,374 KB 给出大于 500 KB 的既有告警，已登记为性能改进项。

## 4. Migration 与数据库一致性

### 4.1 开发库增量升级

- 升级：`sql2014_0018 -> sql2014_0019` PASS；
- `test_run=139`、`unit_result=291127`、`measurement=5578114` 前后不变；
- Current Dataset Version 和 Current Run 均为 10，前后不变；
- Source 级 Current 唯一索引数量为 0；
- `IX_processing_run_source_state` 数量为 1；
- Dataset Current 过滤唯一索引数量为 1；
- Current Version 无 Current Run、Current Run 无 Current Version均为 0。

### 4.2 随机空库

- 随机库名严格匹配 `NCE_TMS_V12_<32HEX>_MIGRATION_TEST`；
- 从空库连续执行 `sql2014_0001 -> sql2014_0019` PASS；
- 后置验证 revision 和两个关键索引 PASS；
- 只删除精确随机库，最后验证数据库不存在。

### 4.3 repair DryRun

- 策略：`DATASET_SCOPED_PROCESSING_RUN_CURRENT_V2`；
- repair/promote/supersede 均为 0；
- Windows 维护检查已补齐反向规则：无 Current+PUBLISHED Dataset 关联的 Run 必须为 `SUPERSEDED/is_current=0`。

## 5. 真实 SQL E2E

| 场景 | 结果 | 关键证据 |
|---|---|---|
| 同 SHA 两连接并发登记 | PASS | 2 次成功、1 Source、2 Batch、2 Receipt，重复标记一真一假 |
| Upload-to-Current 重复上传 | PASS | 真实 HTTP、2 用户、2 实际快照、2 SQL Worker；Worker input resolver 对各自 Receipt 路径和 SHA 校验成功；synthetic Canonical staging 后形成 2 Job、2 Run、2 Dataset，均 Current+PUBLISHED |
| 工程/量产可见性 | PASS | 工程 non-owner 拒绝；量产 Current non-owner 可读但不可管理 |
| Dataset-scoped Current | PASS | 同 Source 的 2 个独立 Run 可同时 Current |
| 同 Dataset V2 | PASS | 只 Supersede 本 Dataset V1，不影响其他 Dataset |
| Atomic Finalize 故障注入 | PASS | 7 个故障点事务回滚，STAGED 恢复 PASS |
| Reprocess | PASS | 成功和回滚边界 PASS |
| Archive | PASS | 成功和回滚边界 PASS |
| 生命周期并发锁 | PASS | 第二会话等待约 0.756 s，无数据变更 |
| v1.1 兼容只读功能 | PASS | 173 条只读语句，0 条被阻止，CP/FT compare/detail/quality 对账 |
| 清理 | PASS | 数据库计数恢复、fixture_rows=0、上传临时根目录不存在 |

重复上传专项故意不重新验证厂家 Cleaner 解析正确性：它在 Worker 领取 Job 后写入最小 synthetic Canonical staging，再调用生产 Atomic Finalizer，以隔离 Source/Receipt/Batch/Job/Run/Dataset 和 Current 语义。真实 CP/FT Cleaner 与 Canonical Writer 继续由既有 Route A 回归负责，不能把本项单独称为真实 Cleaner E2E。

## 6. 权限与可见性矩阵

| 场景 | Owner | Production non-owner | Engineering non-owner | Admin |
|---|---|---|---|---|
| 工程 Current | 读/分析 | 列表隐藏；直达 Dataset 403 | 列表隐藏；直达 Dataset 403 | 读/管理 |
| 量产 Current+PUBLISHED | 读/分析/管理自己的 | 读/分析 | 读/分析 | 读/管理 |
| 量产上传摘要 | 读自己的并管理 | 读共享摘要、不可管理 | 读共享摘要、不可管理 | 读/管理 |
| Draft/历史/失败中间详情 | 读自己的 | 拒绝：Dataset 403；隐私型中间对象 404 | 拒绝：Dataset 403；隐私型中间对象 404 | 读/管理 |
| 原始文件下载 | 自己可下载 | 404 | 404 | 可下载 |
| Input Request prompt/Source | 自己可读写 | 404 | 404 | 可读写 |
| 产品补录/导出/重处理/归档 | 逐行动作允许 | 隐藏并拒绝 | 隐藏并拒绝 | 允许 |

## 7. 性能结果

方法：首次调用记录 cold candidate；随后顺序执行 5 次 warm 样本，记录 p50 和线性插值高位观测值；elapsed 包含服务调用和 JSON 序列化，不刷新 SQL Server/OS cache。`n=5` 只能用于本机开发探针，不能作为正式尾延迟 p95 验收。

| 探针 | Cold ms | Warm p50 ms | Warm 高位观测 ms（n=5） | SQL/次 | 开发参考线 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| Stage CP uploads | 520.629 | 521.697 | 537.502 | 2 | ≤ 3000 | DEV PROBE PASS |
| Stage FT uploads | 518.947 | 537.644 | 541.669 | 2 | ≤ 3000 | DEV PROBE PASS |
| Current Catalog | 40.339 | 40.588 | 49.377 | 2 | ≤ 3000 | DEV PROBE PASS |
| Dataset Chart | 553.876 | 558.679 | 566.243 | 4 | ≤ 3000 | DEV PROBE PASS |
| Dataset Detail | 664.115 | 639.141 | 647.118 | 8 | ≤ 3000 | DEV PROBE PASS |
| Quality Summary | 689.064 | 709.861 | 724.609 | 10 | cold ≤ 5000；warm ≤ 3000 | DEV PROBE PASS |
| 8 Dataset、无参数 | — | — | — | — | ≤ 3000 | SKIP：0/8 兼容数据 |
| 8 Dataset、5 参数 | — | — | — | — | ≤ 5000 | SKIP：0/8 兼容数据 |

只读门禁记录 173 条 SELECT/只读 CTE，阻止计数 0，前后业务表行数一致。

性能结论不能写成“全部 PASS”：6 个有数据的探针只证明本机 5 次 warm 调用达到开发参考线；2 个关键 Compare 场景缺少 8 个同阶段、同 Spec 的 Current Dataset，整体状态为 COVERAGE SKIP。正式 G3 必须在固定数据规模下，以每场景至少 30～50 个样本、并发 1/5 分组测量 p50/p95；页面基础点击反馈的 300 ms 门槛也未取得可排除浏览器控制链路开销的可信测量。

## 8. 浏览器验收

- 工程/量产导航、量产 FT 上传记录、重复来源和上传人显示正常；
- 量产页明确说明共享查询和独立重复分析；
- Current Catalog 显示分析、补录/修正、导出、重处理和归档，并由逐行动作 capability 决定；
- 实际进入 CP Dataset，统计总数、PASS/FAIL、良率和 Unit 明细加载成功；
- 强制鉴权后原 URL 自动显示登录页，旧 Dataset 和管理按钮不残留；
- 浏览器 error/warning console 为空；
- 为避免测试密码传输，没有执行真实两账户浏览器登录。

## 9. 发布包验证

| 项目 | 结果 |
|---|---|
| Release | `v1.2-functional-rc1` |
| Schema | `sql2014_0019` |
| 文件数 | 216 |
| ZIP 大小 | 503,305 bytes |
| Build A SHA-256 | `67732689436002e79bfa3fbd6b7a8f8427ad115fbeb78abb2ca3c7c04d990769` |
| Build B SHA-256 | `67732689436002e79bfa3fbd6b7a8f8427ad115fbeb78abb2ca3c7c04d990769` |
| 可复现 | PASS，大小和哈希一致 |
| Manifest/CRC | PASS |
| 秘密/禁止路径扫描 | PASS |
| 解包 launcher `-ValidateOnly` | PASS |

## 10. 风险与未完成项

- SQL Server `12.0.5000.0` 低于目标 SP3，目标 G3/G4 保持 NO-GO；
- 8-Dataset Compare 和真实用户 A→B 浏览器 UAT 未完成；
- 当前只有开发库响应，未覆盖生产深页、峰值并发和网络时延；
- 既有 3 个 `NEEDS_INPUT` Job（64、66、68）未处理；
- 本轮没有用同一专项脚本重复运行真实 Cleaner；其正确性依赖既有 Route A 回归，两类证据已分开记录；
- Windows Integrated Security 预检不能连接当前远程 SQL 登录面；
- 安全专项、目标备份恢复、灾备演练和生产发布未执行。
