# TMS Formal PAT Adapter Closure — 2026-08-31

## 1. 结论

AC4 / V19 的技术闭环采用版本化 `FORMAL_PAT_SHARED_ENGINE_ADAPTER_V1`，算法代码为 `PAT_SHARED_IQR_1_35_V1`。正式 PAT 读取已固定 Dataset Version、Filter、单一参数和 Rule Version 的 Canonical Measurement；异常点保留 `dataset_id / version_no / unit_id / measurement_id / drilldown_key`，不把 Quick Workspace 当正式事实。

本闭环**没有创建、批准或激活任何 Rule**。没有三方 Owner 批准和 Activation 时，正式 API 继续返回 `ANALYSIS_RULE_NOT_APPROVED`；技术对账通过不等于业务规则已批准。

## 2. 复用边界与版本合同

已验收的来源为 `F:\data_IGBT_multiple\shared\pat_engine.py::compute_pat_stats`：

- 最后变更 Commit：`ebf9c7a05b8a10987941c8dacd7e4b1295ae58c1`；
- 源文件 SHA-256：`b853dd935a2adf75190f25bb664b1e2c27f1c09bb89e2c91b5245799aa9f183a`；
- Quantile：pandas 默认 Linear；
- `Sigma=(Q3-Q1)/1.35`，零离散时 Sigma 为 0；
- `LCL/UCL=Median±6Sigma`；
- 输出保留 6 位小数，边界相等不标记异常；
- 异常只标记，不从总体静默剔除。

TMS 服务运行环境没有 numpy/pandas，且生产服务不应依赖另一个工作区的可变源文件，因此没有在请求时动态导入 `F:\data_IGBT_multiple`。Adapter 使用同源、无第三方依赖的纯函数语义，并冻结来源 Commit、SHA、算法版本和完整 Manifest。Adapter Manifest SHA-256 为：

`3564929accfae8af9745d7ed08f42bc7b08503d17373a8e45d6d7a63bff85c34`

创建该 PAT Rule Version 时，后端要求 `algorithm_sha256` 精确等于上述 Manifest SHA；上下倍数固定为 6，不能借同一算法代码悄然改成另一套公式。Golden SHA 仍由 Quality Validator 独立输入和批准，前端不会预填。

## 3. 对账证据

执行：

```powershell
.\.conda-env\python.exe scripts\g0\verify_formal_pat_shared_engine.py `
  --source-engine F:\data_IGBT_multiple\shared\pat_engine.py `
  --source-python D:\ProgramData\anaconda3\python.exe `
  --quick-pat-summary artifacts\quick_pat_e2e_20260826_final\1787719588\attempt-1\pat_summary.json
```

结果：

- 源文件 SHA 和最后变更 Commit 均精确匹配；
- 4 组正常/插值/零离散/负值与小数 Golden，直接调用成熟 `compute_pat_stats` 后，Q1、Median、Q3、Sigma、LCL、UCL 和异常索引逐项一致；
- 已有真实杰群 Quick PAT 证据：520 个文件、6,813,800 解析行、23 个参数；23/23 参数满足冻结公式；
- Quick PAT Source Manifest SHA-256：`55b9b0e951fc9db8a4f29656509d467c78389025a176e65ac963eac923785355`；
- 对账输出 `status=PASS`、`owner_gate=NOT_BYPASSED`。

## 4. 测试与限制

- Adapter/治理/Rule Pinning/Saved 定向后端：35 tests passed；
- Rule Registry 定向前端：2 files、5 tests passed；
- Adapter、对账脚本和测试 Ruff passed；
- `F:\data_IGBT_multiple` 和 `F:\data` 全程只读，未修改原始数据，也未伪造 Owner 批准。

仍需业务 Owner 明确批准的事项包括：适用 Stage/厂家/产品/参数、分组范围、Minimum N、Retest、缺失值、Spec 与 PAT 层关系，以及该 Golden Manifest。批准前 V19 状态应报告为 **技术已实现、数据可验证、Owner-gated**，不得称为生产规则已启用。
