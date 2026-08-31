# TMS v1.3 分析闭环回归与性能测试报告

- 报告日期：2026-08-31
- 对象：AC1～AC5、V01～V28、前端六分组分析工作台
- 开发数据库：`TMS_G0_DEV`
- SQL Server：12.0.5000.0
- Schema：`sql2014_0023`
- 当前状态：**AC1～AC5 本机技术关闭；G0～G2.5 本机候选 PASS；G3/G4 未执行**

> **最终签发摘要**
>
> - `F01`：后端 `1,011 passed, 4 skipped, 16 warnings` / 43.01 s；前端 48 files / 237 tests，Build PASS；约定范围 Ruff、compileall、diff-check PASS。
> - `F02`：13/13 正式场景 PASS；warmup 2，并发 1/5 各 30 次，0 error、0 blocked，Canonical 不变。
> - `F03`：双 Release 均含 275 个 Manifest payload 文件（ZIP 276 entries）/ 798,680 Bytes，SHA-256 相同；CRC/Manifest/秘密扫描、真实解包 API ready 和残留清理 PASS。

## 1. 测试原则

- `F:\data` 原始 CP/FT 数据只读，不改名、不覆盖、不在源目录写缓存或输出。
- 正式事实只通过 `test.test_run -> test.unit_result -> test.measurement` 对账；Quick Workspace 和 Export Artifact 不作为第二事实源。
- 不用 HTTP 200 或“页面能打开”代替业务验收；同时断言数值、Context、Rule/Spec/Bin 身份、采样摘要、钻取成员和页面可操作性。
- Owner-gated 统计只能来自已批准并激活的 Rule。开发库审批/激活均为 0 时，正确结果是稳定门禁，禁止插入假审批来制造正向结果。
- 未知 Schema、单位、身份、Mapping、Spec、Bin 或计算语义一律 fail closed。
- 性能只报告实测。正式脚本出现任一 `SKIP`、`ERROR`、`THRESHOLD_EXCEEDED` 或稳定摘要不一致时，整体非 PASS。
- 本机 G0～G2.5 结果不外推到 SQL Server 2014 SP3+ TEST、生产网络、正式并发或 G4。

## 2. 数据规模与覆盖

### 2.1 `F:\data` 只读盘点

| 项目 | 只读盘点值 |
|---|---:|
| 根目录 | `F:\data\CP和FT源数据` |
| 目录 | 204 |
| 文件 | 2,970 |
| 总字节 | 6,336,287,297 |
| CP 文件 | 1,468 |
| FT 文件 | 1,502 |
| 路径/大小清单摘要 | `69e1a83956004b647adcc45677f207201144bf11b02e6963a0eee7256a003c4a` |
| 扫描错误 | 0 |
| Reparse Point | 0 |

该摘要是 `RELATIVE_PATH_SIZE_V1`，不是逐文件内容 SHA，不能替代正式 Golden 冻结。已验证的 Route A 正向范围只包括 HUAHONG/JETECH/LION CP 和 RIYUEXIN/RIYUEGUANG DC FT；其他厂商/格式仍保持 Profile/Adapter Gate。本轮没有向源目录写入任何文件。

### 2.2 本轮真实开发库候选

| 数据集 | 状态 | Runs | Units | Measurements | 已验证用途与边界 |
|---|---|---:|---:|---:|---|
| FT Dataset 105～112 | 8 个 Current | 42 | 662,799 | 11,930,382 | 1/多/8 Dataset、明细、参数、关系与大点集；源无 PASS/FAIL/Bin，`overall_result=UNKNOWN`、Yield=NULL |
| CP Dataset 113/V2 | Current；V1 已 Supersede | 1 | 3,875 | 50,375 | Batch 161 / Job 199 / Run 164；25 Wafer × 155 Unit；BVDSS Heatmap、Wafer Summary、明细 |

CP 113/V2 的正式评价分布为 49,475 PASS、100 FAIL、800 NOT_EVALUATED。单位级 Overview 为 PASS 3,775、FAIL 100、Yield 97.419%。BVDSS Heatmap 正向；由于没有版本化 Bin Mapping，Bin Map 正确门控，不把原始 Bin 文本伪装成正式 Mapping。

上述是开发库受控工作负载，不等同于 Data Owner 已签发的正式 Golden。

## 3. Migration、Schema 与 Canonical 不变量

### 3.1 Schema 链

- 起点为 `sql2014_0019`。
- `sql2014_0020_analytics_governance`：规则治理、Saved Analysis 与相关审计合同。
- `sql2014_0021_analytics_export_lifecycle`：Export Job/Artifact 生命周期与审计合同。
- `sql2014_0022_quality_rule_types`：质量规则类型。
- `sql2014_0023_analytics_performance_indexes`：为 Measurement、Unit Result、Bin Evaluation 等真实分析路径增加索引；不新增第二份 Measurement 事实表。

`sql2014_0023` 已在 `TMS_G0_DEV` 应用，迁移约 10.3 秒。迁移前后 Canonical 计数不变：

| 范围 | Test Run | Unit Result | Measurement | Dataset Version |
|---|---:|---:|---:|---:|
| 全量 Canonical | 231 | 961,676 | 17,609,246 | — |
| Current | 117 | 737,561 | 13,232,658 | 19 |

v1.1 兼容只读验证执行 186 条只读 SQL、0 blocked；Canonical/Current/Catalog 摘要前后不变。生产回退不能依靠破坏性 downgrade 猜测历史状态，应采用兼容读取或恢复升级前备份。

### 3.2 真实 SQL 专项与 E2E

| 专项 | 结果 | 数据不变/清理证据 |
|---|---|---|
| Spec Evaluation Materialization | **PASS** | 六状态覆盖；首次/二次均 6 条；事务内幂等；`rollback_clean=true` |
| Bin Mapping Materialization | **PASS** | MATCHED/NO_MATCH/CONFIG_AMBIGUOUS；首次/二次均 3 条；`rollback_clean=true` |
| v1.1 兼容只读 | **PASS** | 186 条只读语句、0 blocked；Canonical/Current/Catalog 摘要不变 |
| v1.3 Parameter Analysis 只读 | **PASS** | CP/FT DESCRIPTIVE 独立对账；168 条只读 SQL、0 blocked；前后规则审批/激活均为 0 |
| Owner Rule Gate | **PASS** | BOX/HISTOGRAM/CAPABILITY/CORRELATION 的伪造精确 Rule 各重复 6 次均稳定拒绝，没有写入假审批 |
| Analytics Export Lifecycle | **PASS** | Schema 0023；受控 Job 6；`recovered_attempt_count=2`、`old_worker_fenced=true`；DryRun 保留文件，Execute 后 `physical_status=DELETED`、`job_status=EXPIRED`；SHA 记录完成，测试创建的受控文件已清理 |
| CP 113 UI Reprocess | **PASS** | 形成 Dataset 113/V2 Current；V1 Supersede；25×155 Unit、50,375 Measurement 与评价分布对账 |
| 前端 Export Job 5 | **PASS** | Delivery 页面提交→Worker 领取→SUCCESS→元数据→下载；Current Page 精确 50 行，排序/焦点/筛选上下文冻结 |

Export Job 5 的可复核元数据：模板 `ANALYTICS_DETAIL@v1`、文件 `analytics-export-5-attempt-1.csv`、8,069 bytes、SHA-256 `5257879395d0911b0cddc4b2bd95b7d98c5556767207afe13091c487f5e2bf97`，CSV 为 50 条数据记录。它是正式 UI 审计记录，其 TTL Artifact 按产品合同保留，不应被写成测试残留，也不纳入 Job 6 的“受控文件已清理”断言。历史损坏 Job 1 被列表隔离并显示明确错误，没有拖垮整个 Export 列表。

## 4. 自动化回归结果

### 4.1 后端与静态检查

最终自动化在性能收口与格式整理后执行；开发文档回填不改变运行源码。

| 项目 | 结果 | 证据/边界 |
|---|---|---|
| Python 全量 pytest | **PASS** | `1,011 passed, 4 skipped, 16 warnings in 43.01s` |
| 4 个 Windows Skip | **环境条件，不阻断本机候选** | 2 个 Analytics Export symlink、1 个 Source Catalog directory symlink、1 个 Golden inventory reparse-point 用例；当前 Windows 账号无 symlink 权限（WinError 1314），对应非链接安全分支与合同测试均通过 |
| Ruff 约定范围 | **PASS** | 所有变更 Python 通过 `E4/E7/E9/F`，仅忽略项目既有路径注入 `E402`；87 个新增 Python 文件通过 format check |
| Python compileall | **PASS** | `backend` 与 `scripts` |
| `git diff --check` | **PASS** | 无补丁空白错误；仅 Git 行尾转换提示 |

仓库级严格 Ruff 探索仍包含既有脚本路径注入 `E402`、FastAPI 风格债务；仓库 format check 仍报告 112 个历史文件未格式化。因此本报告只签发“变更文件约定范围 PASS”，不把它写成全仓全规则或全仓格式 PASS。

### 4.2 前端测试与 Build

| 项目 | 结果 | 证据/边界 |
|---|---|---|
| Vitest 串行全量 | **PASS** | 48 files / 237 tests，0 failure / 0 timeout |
| TypeScript / Vite production build | **PASS** | Production build 成功；未把开发服务器可运行替代生产构建 |
| 组件/失败关闭负例 | **PASS** | Rule Gate、NULL Yield、Context/Export 等合同已纳入自动化与浏览器交叉验证 |

前端最终命令采用单 Worker，避免并发资源竞争把 timeout 误判为断言缺陷：

```powershell
Set-Location frontend
npm test -- --maxWorkers=1
npm run build
Set-Location ..
```

## 5. 数值、PAT 与失败关闭

| 合同 | 已验证结果 |
|---|---|
| Yield | 只用 PASS+FAIL 作分母；全 UNKNOWN/ABORT 时显示 `—`/返回 NULL |
| FT 无源结果 | `overall_result=UNKNOWN`；不从 Spec 反推 PASS 或 Bin |
| Formal Spec | 按 Run 事件时间选 Released/effective 版本；NO_MATCH/CONFIG_AMBIGUOUS/INVALID_VALUE 显式返回 |
| Program Limit | 只标识 `TEST_PROGRAM_CONFIGURATION_NOT_FORMAL_SPEC`；不用于正式 OOS/Cpk/PAT |
| Bin | 只有唯一匹配的版本化 Mapping 可用于正式 Bin/Pareto/Map |
| 聚合钻取 | 返回完整成员键；不使用 first/representative 记录代替成员集合 |
| 大点采样 | 后端确定性；保留全部正式 OOS/Rule-hit 点；前端不先截断后计算统计 |
| Saved/Export | Dataset/Filter/Rule/View State 冻结；Current Page 保留页码、页大小、排序、焦点 Dataset 和评价/Measurement 筛选 |
| PAT | 通过 Adapter 对接成熟 shared PAT engine，不重写统计语义；无批准 Rule 时稳定返回治理门禁 |
| Owner Gate | 开发库 0 审批/0 激活时返回 `ANALYSIS_RULE_NOT_APPROVED` 或规则引用缺失，不返回伪计算值 |

PAT/Box/Histogram/Capability/Correlation 的技术路径与正式规则治理相互独立：算法适配和对账可以通过，但业务正向数值在 Owner 批准前仍保持关闭。

## 6. 前端真实操作 UAT

### 6.1 免登录功能 UAT（G2）

| 任务 | 结果 | 实测事实 |
|---|---|---|
| 四个固定入口 | **PASS** | `/engineering/cp`、`/engineering/ft`、`/production/cp`、`/production/ft` 均可进入；不采用自动类型识别或统一 Cleaner |
| 六分组 | **PASS** | Overview、Detail、Parameter、Spatial、Quality、Delivery 均完成真实点击与数据操作 |
| CP 113/V2 | **PASS** | Overview 3,875 Unit、PASS 3,775、FAIL 100、Yield 97.419%；BVDSS Heatmap 155 点；Bin Map 因无 Mapping 明确门控 |
| CP Wafer Summary | **PASS** | 25 Wafer；Wafer 1 为 PASS 152、FAIL 3、Yield 98.065% |
| FT 105～112 | **PASS** | 8 Dataset 合计 662,799 Unit、全部 UNKNOWN、Yield 显示 `—`；Detail、DESCRIPTIVE 和关系图可操作 |
| Parameter/Rule Gate | **PASS** | FT BVDSS1 为 86,026 行、85,961 numeric、65 missing；伪 Box/PAT Rule 返回 409 `ANALYSIS_RULE_NOT_APPROVED` |
| 钻取与证据 | **PASS** | Detail Drawer 显示 Source、Spec、Release 等证据；Formal Spec 为 RESOLVED |
| Saved Analysis | **PASS** | 创建 R1、恢复、修订 R2、逻辑删除；带伪 Rule 的保存被拒绝 |
| Delivery / Export | **PASS** | 从 UI 创建 Job 5，Worker 完成，浏览器显示 Context/Presentation hash、50 行元数据并完成下载 |
| 历史坏任务隔离 | **PASS** | Job 1 显示单项错误；不使 Export 列表整体失败 |
| Console | **最终功能 UAT 无 error** | 最终代码重启后的新页面复核为 0 error；仅 2 条导航时 ECharts `instance disposed` warning。包内 launcher/API smoke 另行 PASS；本结论仍不外推为生产浏览器零 warning |

VDMOS 没有另设独立菜单；在四个 CP/FT 入口进入“历史正式数据 → 分析”，通过上述六分组查看和操作。

### 6.2 认证与权限 UAT（G2.5）

| 角色 | 结果 | 实测授权/拒绝 |
|---|---|---|
| 未登录 | **PASS** | `/auth/me` 返回 401 |
| SYSTEM_ADMIN | **PASS** | 四入口及管理菜单可见；仍不能绕过未批准 Rule |
| CP_ENGINEER | **PASS** | 四入口、Quick、Current 可见；直接访问 `/users` 返回 UI 403，无规则/用户管理权限 |
| FT_ENGINEER | **PASS** | 授权生产 FT 路径可用；直接访问 `/management/quality` 返回 403 |
| MANAGER_VIEWER | **PASS** | 四入口、Current、Quality Dashboard、Crosswalk 只读；无 Upload、Quick、Operations、Users；`/quick-analysis` 直接访问 403 |
| 身份切换与退出 | **PASS** | 切换身份后旧菜单不残留；退出返回登录页 |
| 临时账号清理 | **PASS** | 随机临时账号 User ID 35～38 已精确 DISABLED，并清空全部角色；密码未写入报告 |

该结论是本机配置认证下的 G2.5，不等同于生产域账号、正式权限清单和目标网络验收。

## 7. 正式性能结果

### 7.1 方法与边界

- 正式命令：warmup=2、iterations=30，分别 concurrency=1 和 5。
- 每个场景记录 cold、warm p50/p95/max、错误率、响应字节、SQL 语句数、返回记录数和稳定采样摘要。
- 任一场景 `SKIP`、`ERROR`、超阈值或稳定摘要不一致时整体必须为 HOLD；本次 13 个场景均未触发这些条件。
- 本机 p95 仅用于进入 G3 评审；G3 仍需目标 TEST 固定数据复测。

正式证据为 2026-08-31 生成的 `artifacts/ac5_performance_20260831/v13_performance_formal_30_final.json`，stderr 为空。JSON 合同本身不声明 started_at/completed_at，因此本报告不把文件时间冒充业务时间戳。脚本执行 9,469 条只读 SQL，阻断 0；执行前后 Test Run 231、Unit Result 961,676、Measurement 17,609,246、Dataset Version 29、Current Published Version 19 均不变。

大 Scatter 为原始 85,624 点、返回 9,514 点；8-Dataset Relationship 为原始 656,185 点、返回 9,407 点，5 个正式 OOS 点全部保留。所有适用场景的采样摘要稳定。

C5 中单参数 `DESCRIPTIVE` 和纯 `SCATTER` Relationship 对完全相同请求采用 single-flight，因此等待请求的 SQL 数可为 0。合并范围严格限定为同一进程、同 Service 类型、同 Engine 实例和完整规范化请求；完成或异常后立即移除，不形成结果缓存。Trend/Correlation、不同 Filter/Version/点数、多个 Uvicorn 进程或异构请求仍分别访问数据库；API 逐请求先完成授权。故 C5 结果不能外推为五条不同查询同时压测数据库的容量结论。

### 7.2 13 个正式场景

下表的 C1/C5 格式为 `p50 / p95 / max ms；每请求 SQL min～max`，每个并发级别均为 30 次实测。

| 场景 | 门槛 p95 | Cold ms | C1 | C5 | 结果 |
|---|---:|---:|---|---|---|
| single_dataset_overview | 3,000 | 1,214.701 | 1,225.387 / 1,248.292 / 1,259.615；7～7 | 1,248.969 / 1,299.803 / 1,308.172；7～7 | PASS |
| single_dataset_detail_200 | 3,000 | 1,921.996 | 1,927.988 / 1,950.627 / 1,968.589；14～14 | 2,006.042 / 2,081.381 / 2,098.580；14～14 | PASS |
| single_dataset_parameter_analysis_up_to_5 | 5,000 | 2,068.782 | 2,092.098 / 2,137.942 / 2,209.951；3～3 | 2,091.137 / 2,124.530 / 2,125.451；0～3 | PASS |
| single_dataset_parameter_relationship | 5,000 | 3,362.151 | 3,270.821 / 3,579.541 / 3,738.291；6～6 | 3,542.379 / 4,462.571 / 4,471.501；0～6 | PASS |
| single_parameter_large_scatter | 5,000；Cold ≤5,000 | 3,219.277 | 3,297.800 / 4,292.057 / 4,599.082；6～6 | 3,451.864 / 4,086.151 / 4,137.572；0～6 | PASS |
| single_wafer_parameter_heatmap | 3,000 | 678.671 | 734.215 / 753.380 / 1,226.610；7～7 | 701.770 / 1,064.862 / 1,277.201；7～7 | PASS |
| multi_wafer_composite_failure | 5,000 | 258.116 | 272.340 / 285.500 / 299.854；6～6 | 2,151.070 / 2,378.645 / 2,396.753；6～6 | PASS |
| wafer_summary_page_200_up_to_5_parameters | 3,000 | 1,363.040 | 1,391.123 / 1,431.697 / 1,448.645；12～12 | 1,385.141 / 2,003.231 / 2,398.500；12～12 | PASS |
| correlation_rule_gate | 5,000 | 534.144 | 538.355 / 546.543 / 554.869；2～2 | 542.254 / 553.433 / 557.301；2～2 | PASS |
| eight_dataset_overview | 3,000 | 570.540 | 565.281 / 614.084 / 675.283；35～35 | 1,072.255 / 1,305.968 / 1,343.741；35～35 | PASS |
| eight_dataset_detail_200 | 3,000 | 1,895.986 | 1,865.246 / 1,947.611 / 1,960.386；49～49 | 2,329.542 / 2,562.477 / 2,579.037；49～49 | PASS |
| eight_dataset_five_parameter_analysis | 5,000 | 4,248.249 | 4,260.448 / 4,349.131 / 4,377.463；2～2 | 4,271.691 / 4,298.095 / 4,299.837；0～2 | PASS |
| eight_dataset_parameter_relationship | 5,000 | 3,318.525 | 3,522.660 / 4,117.312 / 4,494.173；12～12 | 3,690.076 / 3,992.717 / 3,995.431；0～12 | PASS |

正式结果总览：**13/13 PASS，780 个计时请求 0 error，Verification=PASS**。

## 8. Release 与运行验证

旧阶段包不作为本轮发布证据。以下 A/B 包由同一冻结运行源码顺序构建，开发完成报告回填不进入 Release 包。

| 项目 | 最终结果 |
|---|---|
| Release Version | `v1.3-analytics-closure-rc1`；Schema `sql2014_0023` |
| Build A 文件/大小/SHA-256 | `tms-v1.3-analytics-closure-rc1-a.zip` / 798,680 Bytes / `dffd339152e48e66008dcbf2a50b4c8d15f15bc59d20b934481eb61f58940568` |
| Build B 文件/大小/SHA-256 | `tms-v1.3-analytics-closure-rc1-b.zip` / 798,680 Bytes / `dffd339152e48e66008dcbf2a50b4c8d15f15bc59d20b934481eb61f58940568` |
| 双构建可复现 | **PASS**：字节大小与 SHA-256 完全相同 |
| Archive/CRC/Manifest | **PASS**：275 个 Manifest payload 文件、ZIP 276 entries（另 1 项为 `release-manifest.json`）；CRC、逐文件大小/SHA、Manifest 外额外文件检查通过 |
| 原始数据、测试、fixture、mock、本地配置和秘密排除 | **PASS**：禁止路径/后缀、`.env*`、前端测试目录和秘密模式扫描通过；不含 `F:\data`、运行产物或凭据 |
| 真正解包 launcher/runtime health | **PASS**：两次构建均解包，以包内 launcher 启动 API；`/api/v1/health/ready` 返回 ready，数据库 `TMS_G0_DEV`、Schema `sql2014_0023` |
| 运行后残留进程与临时目录 | **PASS**：0 个 release smoke 进程、0 个 `nce-tms-release-*`/`nce-tms-runtime-*` 临时目录；本机 API/前端/Worker 已停止 |

## 9. 当前判定

### 已确定

- Schema 已到 `sql2014_0023`，性能索引迁移没有改变 Canonical/Current 计数。
- CP 113/V2、FT 105～112 的真实数据操作、六分组、四入口、Saved、Job 5 Export 和四角色 G2.5 已完成前端实操。
- Spec/Bin 物化、完整 Export Lifecycle、v1.1/v1.3 只读对账、Owner Gate 与 PAT Adapter 的失败关闭边界已验证。
- `F:\data` 只读盘点未修改原始数据。
- 开发库仍为 0 Rule 审批/0 激活，Owner-gated 正向能力保持关闭。

### 尚未签发

- SQL Server 2014 SP3+ 目标 TEST 的 G3；
- Data Owner 的正式 Golden 与 Rule Owner/Quality Validator 的逐规则签字；
- HTTPS、正式服务账号、备份恢复、安全/容量、正式账号 UAT 与生产 G4。

因此当前结论为：**AC1～AC5 本机技术关闭，G0～G2.5 本机候选 PASS。该结果可作为 G3 申请输入，但 G3/G4 均未执行且当前仍为 NO-GO；Owner/Data Gate 继续关闭。**
