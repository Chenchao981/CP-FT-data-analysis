# 既有 CP/FT Cleaner 真实输出合同（2026-08-21）

> **2026-08-24 Route A 实测补充**：当前发布包仍分别输出 CP 的 `cleaned/yield/spec CSV`，以及 FT 的 `cleaned XLSX + scatter data/spec/manifest`，并不是业务目标描述中的统一三个 XLSX。`sql2014_0010` 因此按实际包登记 `CP_CSV_TRIPLET_V1` 和 `FT_XLSX_SCATTER_V1` 两个版本化输出合同。TMS Worker 按 Cleaner Release 合同读取；后续原 Cleaner 改为 RawData/Spec/Statistics 三个 XLSX 时，必须发布新的 Output Contract Version，不得静默改变旧版本语义。

## 验证结论

TMS 不重写 CP/FT 清洗逻辑。FastAPI Worker 通过独立进程调用两个既有发布包，再把其输出映射到平台数据模型。

## 华虹 CP

- 输入：`NCETEN30CAC_FA5X-2565@203.zip`
- 发布包：`F:\cp_data_ansys\packaging\release\app.pyz`
- 识别：Product `NCETEN30CAC`，业务 Lot `FA5X-2565`，25 片 Wafer。
- 明细：3,875 Die，字段为 Lot/Wafer/Seq/Bin/X/Y 加 15 个测试参数。
- 结果：Pass 3,775，Fail 100，整体 Yield 97.42%，Pass Bin=1，Fail Bin=7。
- 输出：`cleaned CSV`、`yield CSV`、`spec CSV`。
- `spec CSV` 保存 Parameter、Unit、LimitU、LimitL 和多行 Test Condition。

## 日月新 FT DC

- 输入：6 个 Excel，源文件号 `NCT5542087/88/89/90/92/93`。
- 发布包：`F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz`（v2.16.0）。
- 识别：业务 Lot `FA59-3997`，源文件名包含 Product `NCEAP40PT15D(M)-2B00`。
- 明细：35,350 Unit，18 个测试参数；清洗 Excel 实际列为 `NUM`、`lot_ID` 和参数列。
- 输出：清洗 Excel、`ft_scatter_data.csv.gz`、`ft_scatter_spec.csv`、`ft_scatter_manifest.json`。
- `ft_scatter_spec.csv` 保存 Source_ID、Lot、Parameter、Unit、上下限、Bias、Test Condition 和 Source File。
- TMS 专用 Adapter 严格支持已验收的两种文件名方向，并把 Product、Lot、物理测试机号和源文件 Run 身份分别保存；未知文件名或工作表布局停止处理。

## 日月光 FT DC

- 输入：7 个 Excel，Product `NCEA75ED120BT(LA)-3B00`，包含 6 个 Lot、7 个独立源文件 Run、33,064 Unit 和 24 个测试参数。
- 日月光在参数表头中比日月新多一行 `Time`，且 `Unit/Test No.` 所在行不同；Adapter 只在临时副本中去除该行，原始文件哈希保持不变。
- 两个源文件共用物理测试机号 `NCT6528073` 和 Lot `FA54-9815`，但 `HVBCES1/HVBCES2` 的 LSL 分别为 `1.29 kV` 与 `1.27 kV`。因此它们必须作为两个测试 Run 和两个规格指纹处理，不能按测试机号合并。
- `ASE`/`日月光` 只映射到 `RIYUEGUANG`；`日月新` 只映射到 `RIYUEXIN`，两家身份、Release、Adapter、Supplier 和 Dataset 均独立。

## FT 身份与规格补充合同（2026-08-27）

1. `Source_ID` 表示唯一源文件 Run，当前取完整文件名 stem；`tester_id` 只保存物理测试机号（例如 `NCT6528073`）。
2. 每个源文件必须独立生成 `test.test_run`；相同规格指纹可复用 Program Version/Spec Set，不同规格指纹必须隔离。
3. `mdm.spec_binding` 以 `PRODUCT_PROGRAM` 绑定 Product、FT、Supplier、Program Version 与 Spec Set。Dataset 同时存在多套规格时，Dataset Version 不伪造单一 `spec_set_id`，而在元数据中保留逐 Run 绑定。
4. 分析页按源文件 Run 筛选并显示该 Run 的规格线；全范围内存在不同限值时不展示虚假的统一限值。
5. 已批准格式应自动取得 Lot。Lot 缺失或文件名与清洗结果不一致时停止正式入库；人工补录将作为平台侧显式门禁建设，不能以目录名或 `unknown` 代替。

## Adapter 边界

1. CP 与 FT 分开调用、分开映射，不共用源文件 Parser。
2. 发布包在子进程中运行，避免两个项目的包名和依赖互相污染。
3. TMS 只读取已生成结果；原发布包仍是清洗规则的唯一实现来源。
4. TMS Adapter 可以增加厂商身份、已批准布局和交叉对账门禁，但不复制原 Cleaner 的参数、单位或清洗计算。
5. 新格式由独立厂商 Adapter 验收；当前未知格式停止处理，不能自动猜测为相似厂商。
