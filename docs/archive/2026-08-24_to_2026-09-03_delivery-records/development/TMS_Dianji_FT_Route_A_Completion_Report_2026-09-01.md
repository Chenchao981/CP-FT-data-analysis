# TMS 电基 FT Route A 完成报告（2026-09-01）

## 结论

电基 FT 电基数据解析已接入 TMS 既有工程/FT、量产/FT和历史正式数据分析入口。本次实现复用 `F:\data_IGBT_multiple` v2.19.0 成熟 Cleaner，不在 TMS 中重写 PowerTECH Parser、参数识别、单位、限值或清洗算法。

本地技术闭环结论为 **CANDIDATE PASS**：伪 XLS 与原生 XLSX 两类真实样本均已通过 Release 快照、Route A、Canonical、页面结果和分析筛选验证。该结论不等于 G3/G4 生产批准，目标 TEST/UAT、安全、容量和业务签字仍为开放门禁。

## 做了什么

1. 将 `DIANJI` 加入工程/FT与量产/FT的厂家选项，文件合同为 `.xls,.xlsx`；没有新增电基专属菜单，也没有建立统一自动识别 Parser。
2. 登记独立 Cleaner Release：
   - Cleaner：`DIANJI_FT_POWERTECH_EXISTING`
   - Version：`v2.19.0`
   - Adapter：`DIANJI_FT_PYZ`
   - Input Contract：`DIANJI_POWERTECH_DIRECTORY_V1`
   - Output Contract：`DIANJI_FT_SCATTER_V1`
3. Worker 只复制已登记且哈希匹配的源文件到隔离目录，然后调用固定发布包中的 `DianjiDCCleaner` 与 `parse_dianji_source_file`。
4. Canonical Adapter 严格对账 Product、Lot、Source、源格式和每个 Source 的参数规格，并写入唯一正式链路 `test.test_run -> test.unit_result -> test.measurement`。
5. 支持各 Source 动态参数并集：旧 Source 没有后来新增参数时，只允许对应数据为空，并记录 `source_parameter_present=false`；存在数据却无 Source 规格时失败关闭。
6. `CONT*`、`SAME`、`DELAY` 控制项在入库边界再次禁止，避免控制项进入分析参数。
7. 从成熟 v2.19.0 Parser 取得精确 `TestFileName`，Program Version 使用 `<TestFileName>@SPEC-<规格指纹>`。因此前端 Program 筛选能看到 `M08M15`，同时不同规格仍保持隔离。
8. 前端上传对话框明确显示 v2.19.0、PowerTECH 伪 XLS/原生 XLSX、动态参数和 CONT 排除说明；后台入队成功提示包含 Cleaner code/version。
9. Bootstrap 改为必须显式指定 `--factory` 或 `--all`，本次只重登记 `DIANJI`，没有静默替换日月新/日月光发布包。

## 已确定的 Release 事实

| 项目 | 结果 |
|---|---|
| 发布文件 | `F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz` |
| 版本 | v2.19.0 |
| 大小 | 141,636 bytes |
| SHA256 | `CCE726DE758DDE85966FA7C601F455FC3A025F9C095DB860C59AFFDF0B7FB272` |
| Git 来源 | `F:\data_IGBT_multiple` main / `a94e6718d668c18e00a12d1f28eb9176057a52d3`，与 origin/main 一致 |
| 包内容审计 | 72 个条目，禁止条目 0 |
| 动态参数/PowerTECH 定向测试 | 41 passed |
| IGBT 全量测试 | 126 passed |
| dj1-dj9 实际扫描 | 190/190 文件解析成功；1,620,148 源行，1,601,674 保留行；34.344 s；源哈希未变化 |
| 旧包处置 | `packaging\release.zip` 保留但明确排除，不用于 TMS、也不作为交付文件 |

TMS Cleaner Registry 的当前电基 Release ID 为 `34`，快照哈希与上述发布包一致。日月新 Release ID `31`、日月光 Release ID `32` 在本次登记前后未变化。

## 真实 Route A 验证

### 工程 / FT：PowerTECH 伪 XLS，含 M08M15

| 项目 | 结果 |
|---|---|
| Batch / Job | `164 / 202` |
| Dataset | `116 / V1` |
| 原始文件 | `M260616003-005 C207458.07 DC260716090330.xls` |
| 源 SHA256 | `44624703BDBEE945BBDC455F22182872AF977373239619378ED79D6456E729E8`，处理前后相同 |
| Route A 耗时 | 16.062 s |
| Product / Lot | `NCEAP020N10LL(M)-7E00` / `C207458.07` |
| Canonical | 1 Run，517 Unit，9,823 Measurement，19 Test Item |
| 缺失测量 | 12 条，保留为 `MISSING`，没有补零或伪造限值 |
| Program | `NCEAP020N10LL(M)-7E00_ALL_M08M15_Ver1.07_20260520.ptf@SPEC-A13C71D6FCA716EE` |
| 控制项 | 0 |
| 结果语义 | 517 `UNKNOWN`；PASS/FAIL 分母为 0；Yield 为 `NULL` / 页面显示 `—` |

### 量产 / FT：PowerTECH 原生 XLSX

| 项目 | 结果 |
|---|---|
| Batch / Job | `165 / 203` |
| Dataset | `117 / V1` |
| 源 SHA256 | `45A020CB843A5E5A1E1A040E0F4855B69A40A05464D48F21E85D8CF0F5EF2850`，处理前后相同 |
| Route A 耗时 | 17.283 s |
| Product / Lot | `NCE40ED120VT(LA)` / `FA5Y-9412` |
| Canonical | 1 Run，761 Unit，15,981 Measurement，21 Test Item |
| 缺失测量 | 89 条，保留为 `MISSING` |
| 控制项 | 0 |
| 结果语义 | 761 `UNKNOWN`；Yield 为 `NULL` / 页面显示 `—` |

## 前端功能测试

在本地 TMS（API/Worker/Frontend + `TMS_G0_DEV`，`sql2014_0023`）通过应用内浏览器实际操作：

1. 工程/FT 清洗结果显示 Batch `#164`、19 项、517 Unit、Yield `—`。
2. Dataset `116/V1` 分析页显示 517 Unit、0 PASS、0 FAIL、517 UNKNOWN、已知良率 `—`。
3. Program 下拉项显示完整 `M08M15` TestFileName 与规格指纹，不再只显示 `SPEC-*`。
4. 参数搜索 `DVDS` 能找到 `DVDS(mV)`；搜索 `CONT` 无参数选项并显示无数据。
5. 量产/FT 原始文件页显示 Batch `165`、Job `203`、电基和已处理状态；清洗结果显示 21 项、761 Unit、Yield `—`。
6. 量产/FT 上传对话框选择电基后，文件输入 `accept=.xls,.xlsx`，说明包含 v2.19.0、动态参数与 CONT 排除合同。
7. 浏览器 console error 为 0。

## 自动化验证

| 验证项 | 结果 |
|---|---|
| 电基 Runner/Writer/API/Release 定向 pytest | 78 passed |
| 后端最终全量 pytest | 1,021 passed，4 skipped，16 warnings，86.25 s |
| Python compile / Ruff | PASS |
| 前端本次电基功能定向用例 | 3 passed，17 skipped，29.02 s |
| 前端 production build | PASS；13,078 modules；仅既有 chunk-size warning |
| 前端全量 Vitest（低并发） | 233 passed / 240；7 项超时，分散于既有规则、数据集和分析页面；本次电基断言未失败 |

全量 Vitest 的 7 项均为运行时间超限，不是断言不一致；其中 `StageDataWorkbench` 的同一既有目录提交失败用例在文件级定向重跑时仍超时，但本次电基的 3 个精确用例全部通过。它们需要在稳定 CI 资源下再次确认，不能把当前结果写成“前端全量 PASS”。

## 做得好的地方

- 未改动 `F:\data` 原始文件；两条最终 Route A 的输入哈希处理前后完全一致。
- TMS 复用了成熟 Cleaner/Parser，只增加不可变 Release、源身份和 Canonical 合同边界。
- 动态参数缺失没有被补零，未知规格、存在值但无规格、身份冲突和控制项均失败关闭。
- Program 同时保留人可读 TestFileName 与机器可核对规格指纹，解决了 M08M15 前端追溯缺口。
- 电基、日月新、日月光仍使用独立 Release、Adapter、Supplier 和格式合同。

## 不确定项和限制

1. 真实样本证明了伪 XLS 与原生 XLSX，以及产品间参数数不同；“同一产品后续右侧新增新列”的兼容性目前由合成回归覆盖，尚缺供应商真实未来新增列 Golden 样本。
2. `TestFileName` 提取调用了 v2.19.0 包内固定的 Parser 内部读取函数。当前 Release 已通过快照和测试；未来升级 Cleaner 时必须同步做合同兼容检查，不能直接替换包。
3. 电基样本没有经批准的 PASS/FAIL/Bin 语义，因此当前只能做参数、规格和统计分析，不能据此计算正式良率。
4. 本次浏览器验证使用 loopback 免登录的本地测试环境，不代表目标服务器的登录、权限、网络、并发和容量验收。
5. 前端全量测试存在 7 个资源/时序超时项；本次电基定向用例、生产构建和浏览器流程已通过，但全量稳定性门禁仍开放。

## 下一步与开放门禁

1. 取得至少一组真实“原列 + 右侧新增业务参数列”的同产品 Golden 文件，逐项对账列名、单位、限值、行数和 NULL 行为。
2. 由业务 Owner 确认电基 PASS/FAIL、Bin 与正式 Yield 口径；未确认前继续保持 `UNKNOWN` / `NULL`。
3. 在目标 TEST 环境完成登录/RBAC、上传下载权限、Worker 服务账号、审计、容量和恢复性测试。
4. 在稳定 CI 资源下重跑前端全量 Vitest，并单独跟踪现有 7 个超时用例。
5. 完成 Golden 签字、UAT、安全、容量和业务签字后，才能从本地 CANDIDATE PASS 推进到 CANARY/STABLE；不得把本报告当作 G3/G4 生产批准。
