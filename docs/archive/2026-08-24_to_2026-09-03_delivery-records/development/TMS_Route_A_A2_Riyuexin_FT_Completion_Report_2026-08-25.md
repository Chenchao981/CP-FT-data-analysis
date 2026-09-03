# TMS Route A A2 日月新 FT 跑通报告（2026-08-25）

## 结论

日月新 FT 已从工程/量产 FT 上传入口，经现有 Python Cleaner、独立 FT Output Adapter、Worker，跑通到 Canonical 明细、Dataset Current 和 FT 参数分析。本次没有改写 `F:\data_IGBT_multiple` 中稳定的日月新清洗逻辑，只将其四个标准输出结构化接入系统。

## 本次已完成

1. 新增 `FT_XLSX_SCATTER_V1` Writer，严格读取清洗 Excel、scatter data、scatter spec 和 manifest。
2. 校验文件名、批次、Source_ID、参数列、行数、器件唯一键和各 Source Spec；不同 Source Spec 直接拒绝入库。
3. 建立/复用 Product、Supplier、Test Program Version、18 个 Test Item、Spec Set 和 Spec Item。
4. 写入 `test.test_run -> test.unit_result -> test.measurement` 唯一 Canonical 事实链，每个 Source_ID 建立独立 FT Test Run。
5. 源数据无 PASS/FAIL、Bin，因此 `overall_result=UNKNOWN`、`tester_pass_flag=NULL`、`pass_count/yield_rate=NULL`，不伪造良率。
6. 区分 `MISSING` 和实测值，保留 3,348 个缺失测量单元。
7. Worker 已正式调用 FT Writer，成功后自动发布 Dataset Current，不再只保留临时文件和摘要。
8. SQL Server/pyodbc 启用批量写入，本批 636,300 Measurements 的整体 Worker 链路约 40 秒完成。
9. FT 分析页已与 CP 分析分开展示：Lot、Source_ID、参数、单位、测试条件、LSL/USL 和器件散点。
10. 服务器最多向浏览器返回约 10,000 个确定性抽样点，并额外保留所有超规格点；页面显示原始总点数和抽样状态。
11. 工程 FT 和量产 FT 均完成真实 SQL 回填。

## 真实数据验收

数据源：`F:\data\CP和FT源数据\FT数据\日月新\DC`

```text
Product=NCEAP40PT15D(M)-2B00
Lot=FA59-3997
Source files=6
Test Items=18
Units=35,350
Measurements=636,300
Measured cells=632,952
Missing cells=3,348
PASS/FAIL/Bin=source not available
```

六个 Source_ID 器件数对账为 `6,338 / 6,292 / 6,308 / 6,328 / 6,323 / 3,761`，合计 35,350，与 Cleaner manifest 和 Canonical 一致。

```text
ENGINEERING / Batch 12 / Dataset 16 Version 1 / PUBLISHED Current
PRODUCTION  / Batch 13 / Dataset 17 Version 1 / PUBLISHED Current
```

Dataset 16 的真实分析查询已验证：1 Lot、6 Source_ID、18 参数；参数 `VTH1(V)` 共 35,350 个测量点，返回 8,842 个确定性抽样/超规格保留点，规格为 LSL 1.3 V、USL 2.2 V，测试条件 `ID=250uA`。

## 做得好的地方

- 清洗逻辑没有重写，桌面工具与系统使用同一份成熟逻辑，避免两套口径。
- FT 没有被强行套入 CP 的 Wafer/Yield/Bin 语义，未知结果和缺失测量均按真实数据保留。
- 从原始目录到 Cleaner、Canonical、Dataset 和分析查询已用真实数据验收，不是只用构造样本通过单元测试。
- 批量入库性能已能支撑当前日月新数量级，不再是逐行写入导致的不可用速度。

## 完成得不好/尚有限制的地方

1. 本次只验证了日月新 FT。系统架构已保留 Factory + Adapter 扩展点，但其他 FT 工厂还需要各自的标准输出 Writer/映射和真实数据验收，不能因此宣称全部 FT 已完成。
2. 登录后的 FT 页面已通过 TypeScript 编译和 API 数据合同测试，但本次自动浏览器验收停在登录页，没有使用或读取验收账号密码；因此不将“编译通过”表述为“已完成登录后视觉验收”。
3. 页面的大数据抽样保留全部超规格点，但图上“缺失值数”是返回样本内的缺失数，不是全量缺失数；后续可增加服务器全量统计。
4. Worker 已是可循环运行的服务器后台进程，本次未发现断线问题。Windows Server 2019 上的开机自启、失败重启和日志轮转属部署配置，不是日月新 FT 功能缺陷。
5. 前端仍有大包警告，按已冻结决定在功能完成后优化，本次不作阻塞。

## 验证结果

```text
backend unit tests=86 passed
frontend tests=13 passed
frontend production build=PASS
targeted ruff=PASS
real Cleaner output reconciliation=PASS
ENGINEERING FT SQL Worker/CANONICAL/Dataset=PASS
PRODUCTION FT SQL Worker/CANONICAL/Dataset=PASS
real FT chart service query=PASS
authenticated rendered FT page=NOT RUN (no acceptance credential used)
```

## 下一步规划

1. 用正常验收账号完成工程/量产 FT 的登录后页面点击验收。
2. 按实际使用优先级，为其他 FT 工厂逐个接入 Adapter/Writer，每个都使用真实样本对账。
3. 增加 FT 参数全量缺失数、超规格数和超规格比例的服务器聚合。
4. 核心功能完成后，再做前端拆包和更细的 Cleaner/校验/入库分阶段计时。
