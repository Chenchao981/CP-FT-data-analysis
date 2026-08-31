# TMS Analytics Golden Source Manifest（AC0 切片，2026-08-31）

## 1. 结论与边界

本文档固化 v1.3 Analytics AC0 的 **Golden 源候选清单、负例边界和 Owner Gate**。它不是已签字的 Golden expected，也不代表 AC0 关闭、Rule 获批或分析能力可投产。

2026-08-31 对受控源根 `F:\data\CP和FT源数据` 的只读聚合盘点结果是：

- 204 个目录、2,970 个文件、6,336,287,297 Bytes；CP 1,468 文件，FT 1,502 文件。
- 333 个文件命中派生目录、Office 锁文件、辅助文档或过小文件排除规则；2,637 个文件仅是“可继续评估”，不是自动获批的 Golden。
- 遍历成功，未发现链接/重解析点，无扫描错误；库存 `status=PASS` 只表示文件系统盘点完整，不表示格式、语义、Spec、Bin 或统计规则已验收。
- `RELATIVE_PATH_SIZE_V1` 清单摘要为 `69e1a83956004b647adcc45677f207201144bf11b02e6963a0eee7256a003c4a`。该摘要只覆盖相对路径和文件大小，**不是源文件内容 SHA256**，不得用于正式 Golden 内容冻结。
- 本次未输出原始测量值、Lot/序列号、单文件名、账号、连接串或秘密。

## 2. 证据和可复现入口

### 2.1 仓库证据快照

| 证据 | SHA256 | 本文使用方式 |
|---|---|---|
| `docs/development/TMS_Analytics_Closure_Development_Plan_v1.3_2026-08-30.md` | `62cfe6ad3285efa2bf911462f37022e4abfc9d0cf56da7d35159f4dcdaca332c` | Golden 存放、覆盖矩阵、AC0 和 G0-G4 门禁 |
| `docs/development/TMS_Route_A_A2_CP_Runnable_Completion_Report_2026-08-24.md` | `cde363d84a20b2ed317a62ca266b2f46233550241cc71dc333ee46b0d2de9f69` | CP Route A 真实样本历史验收 |
| `docs/development/TMS_Route_A_A2_Riyuexin_FT_Completion_Report_2026-08-25.md` | `84db6e06d1914f4e64586c949f7bb680c8135b29a65976e503ead2439a34f668` | 日月新 DC、`FT_XLSX_SCATTER_V1` 和无 PASS/FAIL/Bin 语义 |
| `docs/development/TMS_Capability_Split_and_Riyueguang_FT_Completion_Report_2026-08-27.md` | `060721b6759c94d941e9d7470db3e7b694ff52fcf19c4de89715d60ea258ea5e` | 当前通用/定制/快速分类与日月光 DC 验收 |
| `backend/app/api/stage_data.py` | `905d8e8c2fa5197bf0f62b8fd2f4c9d9115ffa05e9e5d1292c542dce411eaece` | 当前后端工厂代码和别名白名单 |
| `scripts/g0/inventory_v13_golden_sources.py` | `7c836e6ff6e9636730048e7afc8a8d955b86497716b2b0d10040df7fc5e9fc1c` | 本次只读聚合盘点合同 `TMS_GOLDEN_SOURCE_INVENTORY_V1` |

上述 SHA 是本文成文时快照。后续任一证据文件变更时，必须重新复核引用结论，不能沿用本表 SHA 声称现状未变。

### 2.2 只读重放命令

```powershell
.\.conda-env\python.exe scripts\g0\inventory_v13_golden_sources.py --root 'F:\data\CP和FT源数据' --pretty
```

脚本的安全合同：

- `--root` 必填，不推测也不默认指向真实数据目录；
- 只以 `rb` 打开普通文件，每文件默认最多读取 16 KiB 文件头，不解析或输出测量行/原值；
- 不解压 ZIP/RAR/7z/GZ，不追踪 symlink/junction/reparse point，不对源文件做全文内容哈希；
- 不在 `F:\data` 写文件、日志、缓存或中间产物；JSON 只输出到 stdout；
- 输出只含根目录名、根路径哈希、阶段/供应商目录聚合、扩展名、魔数、脱敏文件头签名计数和排除原因；
- 读失败会给出稳定错误码或 `PARTIAL`，不在错误 JSON 中回显绝对路径。

## 3. 聚合源快照与分类

### 3.1 CP

| 相对源组 | 文件/大小 | 可识别容器或格式 | AC0 分类 | 使用边界 |
|---|---:|---|---|---|
| `CP数据\huahong` | 455 / 63,311,689 Bytes | DCP TXT（362）、ZIP（92）、7z（1） | `VERIFIED_ROUTE_A_SOURCE` | TXT/已批准归档路由历史 Route A 真实样本验证；新 Golden 仍需冻结单独源 SHA、Cleaner Release 和 expected |
| `CP数据\jetech` | 229 / 129,465,608 Bytes | 主要为 OLE `.xls`，并有 RAR/XLSX | `VERIFIED_ROUTE_A_SOURCE` | 只允许已批准 Jetech 格式 Profile；Office 锁文件和过小归档必须作负例/排除 |
| `CP数据\立昂微` | 353 / 38,327,250 Bytes | XLSX（351）、ZIP（2） | `VERIFIED_ROUTE_A_SOURCE` | 只有已批准 CP Measurement 格式可作正向；不与管芯数工具输出混用 |
| `CP数据\国宇FRD` | 144 / 211,244,032 Bytes | OLE `.xls` | `CUSTOM_TOOL_HISTORICAL_ROUTE` | 历史上已跑通 Route A，但当前业务分类已从通用 CP 入口移除；不得作为“当前通用入库”正向 |
| `CP数据\立昂微-管芯数` | 287 / 2,493,588 Bytes | XLSX | `EXCLUDED_DERIVED_CUSTOM` | 独立低频定制能力，整组从常规 CP Die/Measurement Golden 排除 |

### 3.2 FT

| 相对源组 | 文件/大小 | 可识别容器或格式 | AC0 分类 | 使用边界 |
|---|---:|---|---|---|
| `FT数据\日月新` | 200 / 448,992,260 Bytes | XLSX | `VERIFIED_ROUTE_A_SOURCE` 仅 DC | 只有 DC + `FT_XLSX_SCATTER_V1` 已完成正式验收；DVDS/RG/PAT 目录不随 DC 自动获批 |
| `FT数据\日月光` | 38 / 45,404,288 Bytes | DC/DVDS/RG XLSX、HTDC 文本型 `.xls`、HTTF/TF CSV | `VERIFIED_ROUTE_A_SOURCE` 仅 DC | DC 已验收；DVDS、RG、HTDC、HTTF、TF 各需独立 Profile/Adapter/Golden |
| `FT数据\ASE` | 281 / 213,464,028 Bytes | XLSX | `CANDIDATE_RIYUEGUANG_ALIAS` | `ASE` 只可映射 `RIYUEGUANG`；只有获批 DC 子集可继承日月光 DC 路由，不能把整组当作已验收 |
| `FT数据\ASE_mini` | 11 / 6,839,283 Bytes | XLSX | `CI_SUBSET_CANDIDATE` | 读取比对显示其为 `ASE` 的内容子集；可由 Owner 冻结为小样本，但不能作第二份独立业务覆盖 |
| `FT数据\电基` | 310 / 716,311,377 Bytes | PowerTECH 文本型 `.xls`、STS8203/DP1205 CSV、XLSX、GZ/ZIP/JSON | `PROFILE_ADAPTER_REQUIRED` | 同厂多格式必须窄化分派；禁止按扩展名或厂家粗粒度自动合并 |
| `FT数据\集佳` | 5 / 77,604,369 Bytes | STS8203 CSV | `PROFILE_ADAPTER_REQUIRED` | 文件头签名只是结构线索；尚无正式 Route A 验收 |
| `FT数据\杰群data` | 643 / 4,088,786,953 Bytes | DTA Item/Serial CSV 为主，并有 XLS/XLSX | `PROFILE_ADAPTER_REQUIRED` | 快速 PAT 可保持独立路径；未经 Route A 验收不得强制形成全量 Measurement |
| `FT数据\ATX` | 6 / 13,866,326 Bytes | `.log2` + XLSX | `PROFILE_ADAPTER_REQUIRED` | 当前无获批 Adapter；仅作 Profile 候选和未知格式负例 |
| `FT数据\日月光数据示例` | 8 / 280,176,246 Bytes | XLSX/BMP/DOC/RAR/XLS | `EXCLUDED_AUXILIARY` | 文档、图像、归档与演示输出混合，整组不作普通 Golden 正向 |

表中的格式识别仅来自扩展名、魔数和有界文件头签名，不代表已解析完整 schema，也不替代 Cleaner/Adapter Golden 对账。

## 4. 已验证 Route A 正向基线

当前可作为“选取 Golden 源的先决正向”的范围只有：

1. CP `HUAHONG`：已批准 DCP TXT 和已验证归档入口。
2. CP `JETECH`：已批准 OLE XLS Profile。
3. CP `LION`：已批准 CP XLSX Profile。
4. FT `RIYUEXIN`：只有 DC XLSX + `FT_XLSX_SCATTER_V1`。
5. FT `RIYUEGUANG`：只有 DC XLSX + `FT_XLSX_SCATTER_V1`；`ASE`/日月光别名不得映射到 `RIYUEXIN`。

以上“已验证”是 Route A/Cleaner/Canonical/Dataset 的历史技术证据，不是 v1.3 图表 expected 已冻结。每个最终 Golden 仍必须独立记录源内容 SHA、Cleaner Release、Dataset Version、Canonical 数量、Spec/Bin/Rule Version、容差和预期摘要。

国宇 FRD 是重要的历史已跑通证据，但当前业务分类已将它移入定制工具；本 Manifest 不将它写成当前通用 CP Route A 正向。

### 4.1 开发库技术工作负载（非正式 Golden）

本机 `TMS_G0_DEV/sql2014_0023` 已使用下列 Current Dataset 验证 Analytics 技术链，但这些 Dataset 的存在和测试通过**不改变**本 Manifest 的 Owner Gate：

- FT Dataset `105`～`112`：8 个 Current Dataset，合计 42 Test Run、662,799 Unit Result、11,930,382 Measurement；源语义没有 PASS/FAIL/Bin，页面和 API 保持 `overall_result=UNKNOWN`、Yield=NULL。
- CP Dataset `113`：验证对象为 **V2 Current**，对应 25 Wafer、3,875 Unit Result、50,375 Measurement；其中空间坐标与 Wafer Summary 可用，无获批 Bin Mapping 时 Bin Map 继续失败关闭。
- 以上工作负载已用于 Overview、Detail、Parameter、Spatial、Quality、Delivery、Saved Analysis、Export 和浏览器任务测试，只能标记为“开发库技术 workload 已验证”，不能标记为 `OWNER_APPROVED` 或 `GOLDEN_ACCEPTED`。

8-Dataset 技术兼容性已在 FT `105`～`112` 上验证：17 个精确参数 identity 在各 Dataset 间具有相同的单位、测试条件、LSL/USL、比较运算符和边界语义。Dataset 分别绑定 Spec `15`/`16`，技术查询允许在保留各自 Spec 身份的前提下按完整语义签名比较，而不是按 Spec ID 相等或仅按显示名合并。该结论只证明当前开发库工作负载的技术兼容；逐源文件内容 SHA256、Cleaner Release/Dataset Expected、正式兼容清单、Owner/Validator 签字仍未冻结，因此状态仍停留在 `EXPECTED_FROZEN` 之前。

## 5. Profile/Adapter 待验收矩阵

| 优先级 | 范围 | 先置依赖 | 获批前的正确状态 |
|---|---|---|---|
| P0 | 日月光 DVDS/RG/HTDC/HTTF/TF | 每类型独立 Profile、单义 Adapter、身份/单位/测试条件对账 | `FORMAT_NOT_APPROVED`/能力关闭，不回落 DC Adapter |
| P0 | 日月新 DVDS/RG/PAT 相关源 | 同上；同时分离正式入库与快速 PAT | 不继承 DC 批准，不把 PAT 输出当原始 Measurement |
| P0 | 电基 PowerTECH/STS8203/DP1205/原生 XLSX | 狭化格式变体、文件名与内部元数据身份对账、NULL/截尾标记合同 | 工厂不在通用 FT Route A 批准集，禁止提交正式入库 |
| P1 | 集佳 STS8203 CSV | 集佳专属身份、列合同、单位/Spec/Bin 对账 | 相似 STS8203 签名不等于可复用他厂 Adapter |
| P1 | 杰群 DTA CSV | Route A 是否必要的业务决定、Profile、标准输出对账 | 快速 PAT 保持独立；未获批不全量入库 |
| P2 | ATX `.log2`/XLSX | 格式、测试阶段、产品/测试程序身份决策 | 保持未知/关闭，禁止扩展名猜测 |
| 定制 | 国宇 FRD、立昂微-管芯数 | 定制 Web 入口和输出数据合同 | 不进入通用 CP Measurement Golden |

## 6. Fail-closed 负例和排除规则

以下对象应作为负例或在选源前排除，不能因为文件可打开就转为正向：

- 所有 `output`、`PAT2验证`、良率/统计报告、元数据示例、管芯数、文档/图像示例等派生或辅助目录；
- `~$` Office 锁文件、过小占位文件、空扩展名、不在 CP/FT 根下的未分类对象；
- symlink、junction、reparse point、非普通文件或扫描失败项；遇到时跳过并明确告警，不穿透边界；
- 归档仅盘点数量，不解压；归档内容要进入 Golden 时，必须由获批 Adapter 在受管工作区安全解析，并单独验证路径穿越、嵌套归档和解压规模；
- 同一批源的字节级重复或子集不计为独立覆盖。盘点已发现日月新 `DC-6` 与 `DC` 的 6/6 重复，`ASE_mini` 是 `ASE` 内容子集；
- 厂家、Stage、Product、Lot/Source Run、Program/Occurrence/Condition 身份无法唯一对账时失败关闭；已批准格式缺 Lot 只能进入已实现的 `NEEDS_INPUT` 审计闭环，不得用手工值绕过格式/厂家不匹配；
- 缺 Spec、单位冲突、测试条件冲突或多 Lot Spec 不兼容时，依赖该语义的比较/能力失败关闭；不得选第一份 Spec 伪装为统一规格；
- 缺 X/Y 或 Wafer 边界时，Heatmap/Overlay/Zone 等空间能力关闭，但已能证明的普通参数和明细可按稳定原因码降级；
- FT DC 已验收源没有可发布 PASS/FAIL/Bin；`overall_result=UNKNOWN`、良率保持 NULL，禁止补 0%、从 Spec 推断 Pass 或从文件名推断 Bin；
- BoxPlot、Histogram、Correlation、Cpk/Ppk、PAT/SBL、SPC、Margin、Zone 或 Bin 共现的 Rule/Method 未批准时，生产批准集保持空或返回稳定门禁原因，不得代选默认算法。

## 7. Golden 覆盖候选与已知缺口

| 需要的覆盖 | 可评估候选 | 当前结论/缺口 |
|---|---|---|
| CP 单 Lot/单 Wafer，X/Y/Bin/Spec 完整 | 从已验收 HUAHONG/JETECH/LION Profile 中由 Owner 选小集 | 必须重新冻结单文件 SHA 和 expected；库存计数不证明坐标/重复坐标边界 |
| CP 单 Lot/多 Wafer、Overlay/Zone | HUAHONG 已验收多 Wafer 类型 | Wafer 外形、Edge/Center/Quadrant 几何规则和 expected 尚未获 Owner 批准 |
| CP 多 Lot/相同 Spec 与不同 Spec | HUAHONG 多目录源可作选样池 | 哪些 Lot 兼容未冻结；不同 Spec 必须准备负向集 |
| CP 缺 X/Y、缺口/重复/非法坐标 | 尚无已冻结源 | `UNKNOWN_GAP`；需合成负例或 Owner 审批的脱敏真实子集 |
| FT 无 PASS/FAIL/Bin | RIYUEXIN DC、RIYUEGUANG DC | 已知正确 expected 是 Yield=NULL；仍需源 SHA/Dataset/Version 冻结 |
| FT 有正式 PASS/FAIL/Bin | 当前受控源中未找到已发布且语义获批的候选 | `BLOCKED_BY_SOURCE_AND_OWNER`；不得用测量与 Spec 计算值伪造源 Pass/Bin |
| FT 缺失/截尾标记 | 电基 PowerTECH 结构是 Profile 候选 | 当前只有文件头线索；必须先批准 sentinel/NULL 合同与 Adapter |
| FT 多源文件/多机台/多程序/多条件 | RIYUEGUANG DC 已有多 Source Run 历史证据 | 可作候选；须冻结 Run 身份、逐 Run Spec 和不可合并条件 |
| >10,000 点、确定性采样 | RIYUEGUANG/RIYUEXIN DC 和杰群大源 | 前两者可做正式分析候选；杰群需先决定快速 PAT 或 Route A，不得为了性能测试改变业务路由 |
| 参数同名但 Occurrence/Step/Bias/Unit/Condition 不同 | 未完成全源 Profile | `UNKNOWN_GAP`；须在 Profile 层冻结 identity key，禁止仅按显示名合并 |
| PAT/SBL/SPC 正常/边界/负向 | 现有工具输出只能作公式证据候选 | Rule Owner 、版本、输入范围、截尾/NULL 语义、容差和 expected 均未冻结 |
| 8 个同 Stage 兼容 Current Dataset | 开发库 FT Dataset `105`～`112` 已形成 8-Dataset 技术 workload | 17 个参数按 identity、单位、条件、限值/运算符/边界完成技术兼容验证，并保留 Spec `15`/`16` 身份；但源内容 SHA、Expected、正式兼容 Manifest 和 Owner/Validator 仍未冻结，不能写成 `OWNER_APPROVED`/`GOLDEN_ACCEPTED` |

数值边界、超规点、参数缺失比例和分布异常未在本次盘点中读取，因此必须保持 `UNKNOWN`，不得从文件大小或命名猜测。

## 8. 普通 CI、受控 Golden 与专项性能分层

| 层级 | 目的 | 允许输入 | 禁止项 | 通过证据 |
|---|---|---|---|---|
| 普通 G0 CI | 合同、负例、安全边界和确定性 | 仓库内合成/脱敏 fixture；本盘点脚本的 `tmp_path` 合成树 | 不依赖 `F:\data`，不嵌入真实 Lot/测量值，不解压真实归档 | pytest/ruff、稳定 JSON 合同、输入目录前后快照不变 |
| 受控 G1/G2 Golden | 真实 Cleaner→Canonical→Dataset→API/图表对账 | Owner 选定的 HUAHONG/JETECH/LION/RIYUEXIN DC/RIYUEGUANG DC 小集，存放在仓库外受控目录 | 未 Profile/Adapter 格式不作正向；不提交原始数据或可逆 expected | 源内容 SHA、Cleaner Release SHA、Canonical 计数、Spec/Bin/Rule Version、expected 摘要、SQL/浏览器对账 |
| 专项性能 | 大文件、大点集、多 Wafer、8-Dataset、并发和稳态 | 杰群 `520data`（520 个 CSV，约 2.83 GiB）、完整 ASE/日月光候选集、完整多 Lot CP 池等受控源 | 不进入每次提交的普通 CI；不用未批准业务格式假装功能 PASS | 固定规模 Manifest、冷/热运行、查询数、elapsed 分布、内存/临时盘/稳定性；G3 为 30-50 次正式运行 |

专项性能输入必须沿用原业务路由。例如杰群已有快速 PAT 路由时，性能测试不得为了“全量 Canonical”指标强行改变路由。

## 9. Owner Gate 和正式 Golden 冻结字段

### 9.1 必需角色

1. **Source/Data Owner**：批准源范围、数据级别、复制/保留和脱敏方式。
2. **Format/Cleaner Owner**：批准 Profile、Adapter/Cleaner Release、身份、单位和 NULL/sentinel 语义。
3. **Spec/Bin Owner**：批准 Spec Set、逐 Lot/Run 绑定、Pass Bin 和结果状态来源。
4. **Analytics Rule Owner**：批准方法代码、版本、默认关闭行为、容差和适用范围。
5. **Security/Source Custodian**：批准受控路径、最小权限、保留期、审计和不进仓证据。
6. **Acceptance Validator**：独立复核 expected、SQL/API/图表对账、负向和性能结论。

当前本切片没有记录上述 Owner/验收人姓名、批准号或签字日期，因此 AC0 仍未关闭。

### 9.2 状态流转

```text
DISCOVERED
  -> SOURCE_FROZEN
  -> PROFILE_APPROVED
  -> ADAPTER_VERIFIED
  -> EXPECTED_FROZEN
  -> OWNER_APPROVED
  -> GOLDEN_ACCEPTED
```

- 任一前置不满足时不得跳级。
- 技术 Spike 可以用显式测试注入验证 Kernel/SQL，但生产批准集必须默认为空，不得把测试构造器正例称为正式 Golden。
- Rule Gate 未通过、Golden 失败或性能超限时，只关闭相关能力，不得更改 Dataset Current、Canonical 数量或原始文件。

### 9.3 正式 Golden 最小 Manifest 字段

| 类别 | 必填字段 |
|---|---|
| 身份 | `manifest_id`、Stage、Factory、Format/Profile Version、受控 `source_root_code`、相对选择器 |
| 完整性 | 每源文件内容 SHA256、文件数/字节数、重复组、归档内容 Manifest；不在仓库中暴露绝对路径或原值 |
| 处理链 | Cleaner Registry Release ID/Version/SHA、Adapter/Writer Version、Dataset ID/Version/Current/PUBLISHED |
| Canonical | Test Run、Unit Result、Measurement、参数、Lot/Wafer/Source Run 等计数与脱敏摘要 |
| 业务语义 | Spec Set/Binding Version、Bin Version、Result Status 来源、单位/条件/参数 identity key |
| 分析 | Rule/Method Code + Version、Context/Filter、expected 摘要、数值容差、采样版本、能力关闭原因 |
| 治理 | 数据级别、保留期、Owner、Validator、批准日期、证据 SHA、替代/撤回链 |

`RELATIVE_PATH_SIZE_V1` 可用于快速发现目录成员或大小变化，但不能替代上表的每文件内容 SHA256。

## 10. 待关闭项

1. 由 Data Owner 从五条已验证 Route A 范围中指定真实 Golden 小集，冻结内容 SHA 和受控存放位置。
2. 由 Format/Cleaner Owner 确认电基、集佳、杰群、ATX 及两家 FT 非 DC 格式的 Profile/Adapter 优先级；未获批前保持失败关闭。
3. 补齐有正式 PASS/FAIL/Bin 的 FT Golden、CP 坐标负例、参数 identity 冲突、边界/缺失/超规和逐规则正常/边界/负向 Golden。
4. 以已验证的 FT `105`～`112` 技术 workload 为候选，补冻逐源内容 SHA、Cleaner/Dataset Expected、17 参数完整兼容签名、Spec `15`/`16` 身份、Current/PUBLISHED 状态和 Owner/Validator 签字；四角色技术权限矩阵虽已在本机验证，仍需目标 TEST/UAT 的正式账号与直接 URL 负例签发。
5. 分别冻结普通 CI、受控 Golden 和专项性能的规模、频率、资源预算和失败阈值；G3 前完成 30-50 次目标 TEST/UAT 运行。
6. 补全 Rule Owner、API/页面落点、验收人和签字日期。在此之前，本文档只能完成 AC0 的 Source Manifest 切片，不能声称 AC0 整体关闭。
