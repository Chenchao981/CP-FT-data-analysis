# TMS Route A 开发状态（2026-08-24）

依据：`TMS_Business_Requirements_v0.2.md`、`TMS_System_Architecture_v0.7_Route_A.md`、`TMS_Development_Plan_v0.7_Route_A.md`

## 本轮完成范围

A0 基线盘点和 A1 Worker 底座已经完成首轮开发与真实数据库验证。

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

## 实际数据库结果

```text
database=TMS_G0_DEV
revision=sql2014_0010
route_b_detail_tables=0
analysis.saved_analysis=保留
CP Cleaner Release=9 / sha256-78fc9188a96c / CP_CSV_TRIPLET_V1
FT Cleaner Release=10 / sha256-80f3206305cf / FT_XLSX_SCATTER_V1
```

Cleaner Release ID 是当前开发库事实，其他环境由 Bootstrap 脚本按包 SHA256 幂等登记，不应在业务代码中硬编码 ID。

## 验证结果

```text
backend unit tests=72 passed
frontend tests=13 passed
frontend production build=PASS
route_a_schema=PASS
route_a_cleaner_registry=PASS
route_a_initial_worker=PASS
route_a_worker_lease_recovery=PASS
manual_field_enrichment=PASS
integration cleanup=PASS
```

真实 Worker 验证使用已有华虹 ZIP 和已登记 CP Release，生成三个当前格式的结果文件、登记 Artifact 和结果摘要，然后完整清理验证任务与临时目录。

## 当前仍未完成

- A2 的三个 Cleaner 输出 → `test.test_run/unit_result/measurement` 正式结构化 Writer；
- 多 Lot 相同/不同 Spec 的 Lot Binding；
- 首次成功后内部 Dataset Current 自动切换；
- 缺失能力提示弹窗；
- 数据库结构化明细、Yield/Bin/Wafer Map 查询；
- A5 最新版临时导出、显式重清洗原子更新和物理删除；
- 日月新 FT 的 Route A 正式结构化导入；
- SQL Server 正式环境 SP3 升级复验。

当前上传 Worker 仍只形成结果摘要和临时 Artifact，不能把它误称为 A2 正式结构化入库完成。

## 下一开发入口

进入 A2 华虹 CP：先实现 `CP_CSV_TRIPLET_V1` Output Adapter 和运行级参数/Spec 定义，再写入唯一 `test.*` Canonical；使用真实华虹 Golden 样例对账 Lot、Wafer、Die、Bin、X/Y、参数、Measurement、Spec 和 Yield。等原 Cleaner 发布三个 XLSX 新合同后，再增加新的 Output Adapter，不修改旧合同。
