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
- 发布包：`F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz`
- 识别：业务 Lot `FA59-3997`，源文件名包含 Product `NCEAP40PT15D(M)-2B00`。
- 明细：35,350 Unit，18 个测试参数；清洗 Excel 实际列为 `NUM`、`lot_ID` 和参数列。
- 输出：清洗 Excel、`ft_scatter_data.csv.gz`、`ft_scatter_spec.csv`、`ft_scatter_manifest.json`。
- `ft_scatter_spec.csv` 保存 Source_ID、Lot、Parameter、Unit、上下限、Bias、Test Condition 和 Source File。
- 当前清洗 Excel/manifest 没有独立 Product 字段。TMS 可从文件名提出候选并展示确认，也允许人工补录；未经确认不写成源文件事实。

## Adapter 边界

1. CP 与 FT 分开调用、分开映射，不共用源文件 Parser。
2. 发布包在子进程中运行，避免两个项目的包名和依赖互相污染。
3. TMS 只读取已生成结果；原发布包仍是清洗规则的唯一实现来源。
4. 新格式达到一定数量后再引入格式识别器；当前未知格式进入人工选择或待确认。
