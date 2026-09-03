# TMS 能力分类与日月光 FT 正式入库完成报告（2026-08-27）

> **重要更新 / 部分内容已被取代（2026-08-27）**：本报告仍是“能力分类与日月光 FT 正式入库”阶段的历史验收记录，但其中“Lot 人工补录尚未实现、缺失即失败”的表述已经失效。Lot 缺失现在会进入 `NEEDS_INPUT`，用户补录后由子 Job 恢复原 Cleaner Release，并在成功后发布 Dataset Current。当前合同见 `docs/architecture/TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md`，真实完成证据见 `docs/development/TMS_Lot_Input_Recovery_Completion_Report_2026-08-27.md`。

## 1. 完成结论

本阶段已把“通用正式入库、定制工具、快速分析”拆成三类能力，并完成日月新/日月光两个独立 FT DC 正式入库链路。后端、SQL Server、前端和真实样本均已联调；日月光同测试机号下的两次测试 Run 和两套规格能够独立保存、筛选和显示。

## 2. 业务边界落地

1. 通用 CP 正式入口只保留华虹、Jetech、立昂微。
2. 国宇 FRD Excel 清洗与立昂微-管芯数归为定制工具，不再出现在通用 CP 新建或重新处理入口；历史数据、Cleaner 和 Release 均保留。
3. 通用 FT 正式入口本阶段开放日月新、日月光；电基、集佳、杰群在能力页显示状态，但未完成独立 Route A 验收前不能提交正式入库。
4. 杰群 Quick PAT 保持快速分析通道，不强制形成全量 Measurement。
5. `ASE`/`日月光` 只映射 `RIYUEGUANG`；`日月新` 只映射 `RIYUEXIN`。
6. 已批准格式应自动取得 Lot。缺失 Lot 时停止当次发布并进入 `NEEDS_INPUT`；平台人工补录闭环已在同日后续增量完成。身份对账失败仍必须失败关闭，不能通过 Lot 补录绕过格式或厂家不匹配。

详细业务基线见 `docs/business/TMS_Capability_Classification_v1.0_2026-08-27.md`。

## 3. 后端与数据库实现

- Stage API 与 Cleaner Registry 增加 `riyueguang`，并把 `日月光/ASE` 与 `日月新` 的别名彻底分开。
- FT 服务器目录只读取当前目录的直接 XLSX，避免递归混入 DVDS、RG 等其他数据类型。
- Worker 从 FT v2.16.0 发布包调用 `RiyuexinTmsDCCleaner` 或 `RiyueguangTmsDCCleaner`。
- `test.test_run` 一源文件一 Run；`Source_ID` 为完整文件 stem，`tester_id` 为物理测试机号。
- 规格指纹相同的 Run 可复用 Program Version/Spec Set；规格指纹不同的 Run 使用独立 Program Version/Spec Set。
- `mdm.spec_binding` 使用 `PRODUCT_PROGRAM` 绑定 Product、Supplier、FT、Program Version 和 Spec Set。
- 同一 Dataset 含多套规格时，Dataset Version 的单一 `spec_set_id` 留空，并在元数据中保存逐 Run 规格绑定，避免伪造统一规格。
- FT 分析服务按源文件 Run 筛选，并使用对应 Program Version 的单位、限值和测试条件；全范围限值不一致时不显示虚假的统一规格线。

## 4. 前端实现与浏览器验收

- 新增“能力与定制工具”页面，明确展示通用能力、待验收厂家、定制工具和 Lot/Spec 门禁。
- 工程/量产 CP 上传下拉框只显示华虹、Jetech、立昂微。
- 工程/量产 FT 上传下拉框只显示日月新、日月光。
- 修复开发验收模式的前后端身份联动：后端关闭认证时，前端会主动读取开发管理员身份；正式开启认证时仍使用原登录流程。
- 浏览器实测发现并修复 CP→FT 页面切换后残留“立昂微”表单状态的问题。
- 日月光真实结果页显示 24 个参数、33,064 Unit；分析页显示 7 个源文件 Run，而不是错误合并成 6 个 tester。
- 浏览器分别选择两个 `NCT6528073` 源文件，`HVBCES1(kV)` LSL 正确显示为 1.29 kV 与 1.27 kV。
- 最终 Dataset 20 的 33,064 点参数分析请求约 2.07 秒完成；浏览器控制台无 error。

## 5. 真实 SQL Server 验证

### 日月光

- 最终发布包验证：Import Batch 34，Processing Job 48。
- Dataset 20，Dataset Version ID 21，Version 1，状态 `PUBLISHED` 且 Current。
- Supplier=`RIYUEGUANG`，Product=`NCEA75ED120BT(LA)-3B00`。
- 6 个 Lot、7 个 Test Run、6 个物理 tester、7 个 Source Run。
- 33,064 Unit、793,536 Measurement、24 个测试参数。
- 2 个 Program Version、2 个 Spec Set；Dataset Version 的 `spec_set_id=NULL`。
- Run 178：Source `...175110`，Program Version 17，Spec Set 9，HVBCES1 LSL=1.29 kV。
- Run 179：Source `...210319`，Program Version 16，Spec Set 8，HVBCES1 LSL=1.27 kV。

第一次日月光真实任务（Batch 31）因旧 Source_ID 只取 tester、导致规格冲突而失败。该失败记录保留为审计证据；修正为源文件 Run 身份后，最终发布包的 Batch 34 完整通过，没有删除历史失败数据。

### 日月新

- 最终发布包验证：Import Batch 35，Processing Job 49。
- Dataset 21，Dataset Version ID 22，Version 1，状态 `PUBLISHED` 且 Current。
- Supplier=`RIYUEXIN`，Product=`NCEAP40PT15D(M)-2B00`，Lot=`FA59-3997`。
- 6 个 Test Run、35,350 Unit、636,300 Measurement、18 个测试参数。
- 1 个 Program Version，Dataset Version 绑定 Spec Set 10。

### Cleaner Release

- Riyuexin Release 22：`RIYUEXIN_FT_PYZ`。
- Riyueguang Release 23：`RIYUEGUANG_FT_PYZ`。
- 两者均为 `RELEASED`，版本 `sha256-2f052c54c559`，完整 SHA256 为 `2f052c54c559191b358951b10c691a0f81e49170efb5d8a72d529db87821124d`。

## 6. 自动化验证

- TMS 后端全量：117 passed，4 warnings。
- TMS 前端全量：8 test files、19 tests passed。
- 前端 TypeScript 检查与 Vite production build：通过。
- FT Cleaner 全量：104 passed，52 warnings。
- FT v2.16.0 发布包：71 个条目，禁止数据/输出/测试/缓存/日志/文档条目 0，常见硬编码凭据命中 0。
- 最新发布包单文件实跑：3,900 Unit、24 参数、Source_ID 完整、输入副本哈希不变。

warnings 主要来自 openpyxl 的 `datetime.utcnow()` 弃用提示；前端构建仍有既有的大 chunk 提示，不影响本次功能，但需要后续做分包优化。

## 7. 做得较好的地方

- 先用真实文件头部和文件名确认厂商差异，再写 Adapter，没有因为两家格式相似而复用错误身份。
- 首次 SQL 失败暴露了 tester 与 source run 的区别，修复后同时校正了入库、规格绑定和分析筛选三层契约。
- 浏览器验收发现了自动化测试未覆盖的登录联动和跨页面表单残留，并在交付前修复。
- 原始文件没有被修改；发布包经过内容审计和真实包运行后才登记为正式 Release。

## 8. 不确定性与未完成项

- ~~Lot 人工补录的页面、审计记录和重跑闭环尚未实现；当前策略是缺失即失败关闭。~~ **已被同日后续增量取代**：页面、审计记录、父子 Job 恢复和 Dataset Current 发布闭环已实现并完成真实浏览器验收。
- 日月光只完成 DC；DVDS、RG、HTDC、TF 未验收。
- 电基、集佳、杰群正式 Route A 未完成，当前只展示真实状态。
- 国宇 FRD 与立昂微-管芯数本阶段完成分类和入口隔离，尚未迁移为新的 Web 定制工具页面。
- fjd 项目的成熟图表尚未逐项适配 TMS Dataset API；不能把已有前端图表等同于已完成数据库融合。
- 当前 FT 源数据没有可发布的 PASS/FAIL 或 Bin，因此良率保持未知，不做推断。

## 9. 下一阶段顺序

1. Lot 缺失人工补录、审计和安全重跑闭环已完成；后续只处理完成报告中列明的生产门禁与纠错边界。
2. 分别接入电基、集佳、杰群正式 FT Route A，每家使用真实样本和 Golden Manifest 验收。
3. 为国宇 FRD、立昂微-管芯数建立独立 Web 定制工具入口，输出不进入通用 CP Measurement。
4. 按 Dataset API 逐项迁移 fjd 图表，优先复用已经正式入库的 CP/FT 数据，不复制清洗逻辑。
