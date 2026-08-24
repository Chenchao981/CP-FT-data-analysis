# TMS Route A 开发状态（2026-08-24）

依据：`TMS_Business_Requirements_v0.2.md`、`TMS_System_Architecture_v0.7_Route_A.md`、`TMS_Development_Plan_v0.7_Route_A.md`

## 本轮完成范围

A0、A1 已完成；A2 华虹 CP 首条可运行纵向链路已经完成真实数据库和界面验证。

### A0 证据

- `TMS_G0_DEV` 升级前 revision 为 `sql2014_0009`；
- Route B 的 `analysis.run/unit/test_item/measurement` 均为 0 行；
- 未发现引用这四张表的外部 View/对象；
- `analysis.saved_analysis` 属于分析配置而非第二套明细事实，予以保留；
- 当前 CP 发布包实测合同为 `cleaned/yield/spec CSV`；
- 当前 FT 发布包实测合同为 `cleaned XLSX + scatter data/spec/manifest`；
- 当前发布包与业务目标“三个 XLSX”不一致，已按真实输出分别登记版本化合同，后续 Cleaner 更新必须发布新合同版本。

### A1 已实现

- `sql2014_0010` 前向 Migration；
- 删除空的 Route B 明细表，`test.*` 成为唯一 Canonical 明细入口；
- 移除“CP 必须有 Lot、FT 必须有 Product”的旧数据库硬约束；
- Cleaner Release 增加 Factory、Runtime、Entrypoint、Adapter、输入/输出合同、超时和最大输出体积；
- 当前华虹 CP、日月新 FT 发布包按 SHA256 登记为 Released；
- Cleaner 启动前校验发布包 SHA256；
- Cleaner 执行参数由 Release 配置冻结，不再在调用流程中写死具体取值；
- 输出文件角色完整性、文件 SHA256 和总大小校验；
- SQL Server Job Queue 增加幂等键、租约、心跳、重试次数和过期恢复；
- Worker 只领取自身已注册的任务类型，不会误消费尚未实现的导出或历史任务；
- 独立 Route A Worker 与实际华虹 CP Cleaner 联调通过；
- 上传 API 改为登记任务并快速返回 `QUEUED`，不再在 Web 请求中执行 Cleaner；
- 前端增加排队/处理中状态和自动刷新；
- 上传列表、结果、文件下载、Dataset 和补录收紧为 Owner 或 System Admin；
- CP 增加缺失 Lot_ID 的任务级补录字段；
- Worker 临时 Artifact 带 SHA256 和到期时间登记。

### A2 华虹 CP 已实现

- `CP_CSV_TRIPLET_V1` Output Adapter 严格读取 cleaned/yield/spec 三类 CSV；
- 多批次 Spec 沿用第一批次 Spec，并在 Dataset Version 元数据中记录 `FIRST_BATCH`；
- Cleaner 结果写入 `test.test_run/unit_result/measurement` 唯一 Canonical；
- 首次成功自动发布 Dataset Current，结果摘要关联 Dataset ID 和 Version；
- 数据结果页增加“数据分析”入口，自动带入对应 Dataset/Version；
- 数据分析页已能查询 Lot、Wafer、Die、Yield、Bin、参数趋势、Pareto 和 Wafer Map；
- “重新处理”改为异步排队，并复用首次上传的同一条 Route A 入库链路。

## 实际数据库结果

```text
database=TMS_G0_DEV
revision=sql2014_0011
route_b_detail_tables=0
analysis.saved_analysis=保留
CP Cleaner Release=9 / sha256-78fc9188a96c / CP_CSV_TRIPLET_V1
FT Cleaner Release=10 / sha256-80f3206305cf / FT_XLSX_SCATTER_V1
```

Cleaner Release ID 是当前开发库事实，其他环境由 Bootstrap 脚本按包 SHA256 幂等登记，不应在业务代码中硬编码 ID。

## 验证结果

```text
backend unit tests=74 passed
frontend tests=13 passed
frontend production build=PASS
route_a_schema=PASS
route_a_cleaner_registry=PASS
route_a_initial_worker=PASS
route_a_worker_lease_recovery=PASS
manual_field_enrichment=PASS
integration cleanup=PASS
```

真实 Worker 验证使用已有华虹 ZIP 和已登记 CP Release，生成三个当前格式结果文件，完成 Artifact、结果摘要、Dataset Version 和 Canonical 明细写入。另将已有工程 CP 批次 10 正式回填为 Dataset 9 Version 1，并在实际浏览器中完成分析界面验证。

## 当前仍未完成

- 缺失能力提示弹窗；
- A5 最新版临时导出、显式重清洗原子更新和物理删除；
- 日月新 FT 的 Route A 正式结构化导入；

已冻结且不再阻塞当前开发：

- CP/FT 保持两个独立程序，工程/量产 × CP/FT 四个入口直接确定数据类型；
- 多批次分析沿用第一批次 Spec，业务端只选择相同 Spec 批次；
- 隔离内网数据库安全不再作为本阶段讨论项；
- 前端包体积在功能完成后再优化。

## 下一开发入口

进入日月新 FT 的 Route A 正式结构化导入。FT 保持独立 Cleaner 和明细适配逻辑，不与 CP 合并；工程/量产入口继续直接确定业务域。
