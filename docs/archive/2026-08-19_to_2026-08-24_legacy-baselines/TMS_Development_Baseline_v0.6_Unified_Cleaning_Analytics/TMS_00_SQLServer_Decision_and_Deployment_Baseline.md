# TMS SQL Server 选型与部署基线

> 版本：v0.6  
> 目的：固定 TMS 数据库技术路线与部署边界，作为后续开发、测试、上线的共同约束。

---

## 1. 结论

TMS 数据库采用 **Microsoft SQL Server** 是合理且适合当前场景的选择。

原因不是“SQL Server 比 PostgreSQL 更适合结构化数据”，而是以下因素叠加：

1. 数据库保存的是经过 Parser 清洗、标准化后的 Canonical Data，而不是直接保存厂商异构文件结构。
2. 系统是公司内部 TMS，用户数量有限，典型查询以批次、Wafer、参数、Bin、时间范围的聚合分析为主。
3. 最大事实表 `test.measurement` 是分析型事实表，但首版运行于 SQL Server 2014，需要兼顾按 Unit/Parameter 钻取，因此采用 Rowstore + B-tree Index。
4. 主数据、Run、Unit、Spec 等表仍适合 Rowstore。
5. Python/FastAPI/Polars/Pandas 可以继续作为数据解析、统计算法与 API 层，不受数据库切换影响。
6. 如果企业已有 Windows Server、SQL Server、SSMS、备份和运维体系，引入成本低于新增一套 PostgreSQL 运维栈。

### 最终推荐

```text
目标实例：SQL Server 2014 SP3+，Compatibility Level 120
开发/测试：使用隔离数据库，不与现有业务库混用
生产 Edition：以现有实例 Edition 与公司授权核验结果为准
Measurement：Rowstore + 普通非聚集索引，首版不建 Clustered Columnstore
```

> SQL Server 2014支持普通非聚集索引；限制仅针对已经使用Clustered Columnstore的同一张表。首版不在Measurement使用CCI，后续是否建立独立分析表或升级Columnstore由真实压测决定。

---

## 2. 数据库保存什么，不保存什么

### 2.1 SQL Server 保存

SQL Server 只保存标准化、结构化后的数据：

```text
Supplier / Product
Test Program / Version
Spec / Bin Definition
Source File Metadata
Test Run
Unit / Die
Test Item
Measurement
Traceability
Derived Analytics Summary（可重算）
```

### 2.2 SQL Server 不保存原始文件正文作为主数据

华虹 TXT、日月新 XLSX、ZIP、CSV、STDF 等原始文件应保存在：

```text
NAS / 文件服务器 / MinIO / 对象存储
```

SQL Server 仅保存：

- 原始文件名
- 文件大小
- SHA256
- 存储 URI/路径
- Parser 名称与版本
- Parse Status
- 原始 Metadata
- 数据导入批次

这样既能追溯，又避免把数据库变成文件仓库。

---

## 3. 为什么清洗后的数据适合 SQL Server

原始厂商文件可能是：

```text
华虹 CP TXT
日月新 FT XLSX
其他厂商 CSV / DAT / STDF
```

但进入数据库之前统一经过：

```text
Detect Vendor Format
       ↓
Parse
       ↓
Normalize Field Names
       ↓
Normalize Unit / Value / Status
       ↓
Map Product / Program / Test Item / Bin
       ↓
Validate
       ↓
Canonical Model
```

因此数据库面对的并不是异构格式，而是稳定关系模型：

```text
RUN → UNIT → MEASUREMENT → TEST_ITEM
```

这削弱了必须依赖 PostgreSQL JSONB 灵活性的理由。

---

## 4. SQL Server 2014物理存储基线

### 4.1 Rowstore 表

以下表以点查、Join、主外键、维护操作为主，应使用传统 Rowstore：

```text
mdm.supplier
mdm.product
mdm.product_alias
mdm.test_program
mdm.test_program_version
mdm.test_item_definition
mdm.spec_set
mdm.spec_item
mdm.bin_definition

ingestion.import_batch
ingestion.parser_profile
ingestion.source_file

test.test_run
test.unit_result
test.measurement
trace.unit_traceability
```

### 4.2 Measurement首版实现

```text
test.measurement = Rowstore
Clustered Key = measurement_id（最终以压测确认）
Nonclustered Index = unit_id + test_item_id 等已验证访问路径
```

特点：

- 行数远大于其他表。
- 主要是批量追加。
- 查询常扫描大量数据。
- 常执行 AVG / STDEV / COUNT / GROUP BY / Distribution 等分析。
- 很少逐条更新。

SQL Server 2014支持普通非聚集索引，但使用Clustered Columnstore的表不能再创建普通B-tree非聚集索引。TMS需要高频按Unit/Parameter钻取，因此首版明确选择Rowstore，不在Measurement建立CCI。

### 4.3 不要一开始过度优化

完成1M/10M/50M行装载、点查、范围筛选和聚合压测后，允许评估独立汇总表或分析副本；不得在没有回归测试的情况下直接把生产Measurement转换为CCI。

---

## 5. SQL Server Edition 建议

### 5.1 Developer

用途：

```text
开发机
测试环境
CI测试
性能验证
```

不用于正式生产。

### 5.2 Express

可以用于：

```text
个人原型
功能验证
短期 MVP
```

但不建议把长期 CP/FT 参数明细生产库建立在 Express 上，主要原因是数据库容量和资源限制会较早遇到瓶颈，且缺少 SQL Server Agent 等生产运维能力。

### 5.3 Standard

推荐作为正式 TMS 生产版本。

适合：

- 公司内部数人到数十人使用
- 数千万到数亿测试参数明细
- SQL Agent
- 标准备份/恢复
- Windows 运维体系

Enterprise 暂不作为 TMS 第一阶段必要条件。

---

## 6. 推荐服务器逻辑架构

```text
                 TMS Application Server
                Python / FastAPI / Worker
                         │
                         │ TCP 1433 / TLS
                         ▼
                 SQL Server Standard
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
     DATA              LOG              tempdb
       │                 │                 │
 SSD/NVMe            SSD/NVMe          SSD/NVMe
       │
       ▼
 SQL Backup → NAS / Backup Server

原始 CP/FT → NAS/MinIO（数据库只记录 URI + Hash）
```

如果只有一台服务器，至少逻辑上保持：

- Data 文件与 Log 文件分开规划。
- `tempdb` 单独关注空间与 IO。
- 原始文件不要放 SQL MDF 中。
- SQL Backup 不只保存在本机。

---

## 7. 数据库命名与 Schema

数据库建议：

```text
TMS
```

Schema：

```text
mdm         主数据与定义
 ingestion  数据接入与血缘
 test       测试事实层
 trace      CP/FT/封装追溯
 analytics  派生视图/汇总
```

不使用 `master` 作为业务 Schema 名，避免与 SQL Server `master` 系统数据库混淆。

---

## 8. 容量估算方法

TMS 最大增长来自：

```text
Measurement Rows
≈ Tested Units × Average Test Items
```

例：

```text
1,000,000 Unit × 30 Parameter = 30,000,000 Measurement
10,000,000 Unit × 30 Parameter = 300,000,000 Measurement
```

因此容量管理应围绕 measurement，而不是 Lot/Wafer 表。

需要持续监控：

- measurement 行数
- 日/月增量
- Rowstore页数、碎片、索引大小和使用情况
- 数据文件增长
- Log 文件增长
- tempdb
- Import Batch 耗时
- 查询 P95

---

## 9. 批量导入原则

禁止：

```text
for each row:
    ORM INSERT one row
```

推荐：

```text
Parser
  ↓
DataFrame / Arrow Table
  ↓
Staging/Bulk Buffer
  ↓
SqlBulkCopy / bcp / BULK INSERT
  ↓
正式表
```

即使使用Rowstore，也必须批量装载，减少逐行往返、日志开销和索引维护抖动。

建议单次批量规模根据文件和内存实测；目标是减少大量微小 Insert。

---

## 10. 分区策略

**第一阶段不强制分区。**

Rowstore配合必要索引可以支持MVP；过早分区会增加DDL、Filegroup和索引维护复杂度。

当出现以下需求时再启用：

- measurement 达到数亿行以上
- 强烈按年月做生命周期管理
- 需要快速归档/切换历史数据
- 单表维护时间不可接受

推荐的分区字段优先考虑：

```text
measurement.tested_date_key
或 run.started_at 派生的月份键
```

不建议直接按 Supplier / Product 分区，因为产品和供应商数量及数据倾斜不可控。

---

## 11. JSON 使用边界

JSON 只存“扩展属性”，例如：

```text
Bias Conditions
Vendor Metadata
Parser Detection Info
Unknown Future Attributes
```

不应使用 JSON 保存核心测量值：

```text
错误：measurements = {"VTH": 1.8, "BVDSS": 700, ...}
```

核心参数仍采用 Long Format。

SQL Server 2014没有内置JSON函数。首版使用：

```sql
NVARCHAR(MAX)
```

JSON合法性和JSON Schema由应用层在写入前严格校验；需要查询、关联、唯一约束或业务判定的字段必须结构化建列/建表，不能只放在JSON中。

---

## 12. 备份建议

正式环境最低要求：

```text
FULL：每日
DIFF：可选，每 4~12 小时
LOG：若采用 FULL Recovery，15~30 分钟
```

根据企业 RPO/RTO 调整。

必须做：

- 定期 RESTORE 验证，而不是只看 Backup Success。
- 备份文件复制到另一台服务器/NAS。
- 原始 CP/FT 文件和 SQL Backup 分别保护。
- 记录数据库版本、DDL版本和 Parser版本。

---

## 13. 安全基线

- 应用使用独立 SQL Login/AD Account，不使用 `sa`。
- 应用账号默认不具备 DDL 权限。
- Parser/ETL 账号仅允许写 ingestion/test 所需对象。
- 分析只读账号授予 analytics/test SELECT。
- 生产启用 TLS。
- 密码/连接串不写入 Git。
- 数据库备份按公司敏感数据标准保护。
- 终端用户认证、RBAC 和 Data Scope 由应用 IAM 层负责，不能用共享 SQL Login 代替用户身份。
- 每次访问 Source File、Dataset Version、Measurement、Saved Analysis 和 Export Artifact 都执行对象级授权。
- 管理员角色不自动拥有业务明细下载权限；导出权限独立授予。
- 本地账号只保存 Argon2id 或同等级不可逆哈希；优先评估公司 AD/LDAP/OIDC。
- 下载链接短时有效，不暴露 NAS/MinIO 物理路径。

---

## 14. 技术边界

SQL Server 负责：

```text
存储
过滤
Join
Group By
Window Function
基础聚合
索引
约束
事务
权限
```

Python 分析层负责：

```text
CPK/Ppk高级口径
PAT/SBL
复杂SPC规则
相关性矩阵
异常检测
Wafer Pattern
统计检验
AI分析
报告生成
```

避免把所有统计算法都写成复杂 T-SQL Stored Procedure。

---

## 15. 官方参考

- Microsoft Learn：Columnstore indexes overview  
  https://learn.microsoft.com/sql/relational-databases/indexes/columnstore-indexes-overview
- Microsoft Learn：SQL Server 2025 What's New  
  https://learn.microsoft.com/sql/sql-server/what-s-new-in-sql-server-2025
- Microsoft Learn：SQL Server editions and supported features  
  https://learn.microsoft.com/sql/sql-server/editions-and-components-of-sql-server-2025

---

## 16. 设计冻结原则

后续增加其他晶圆厂/封测厂文件时，优先级是：

```text
新增 Parser
→ 新增 Mapping
→ 补充 metadata/alias
→ 验证 Canonical Model
```

只有出现当前模型无法表达的新业务语义时，才修改核心 Schema 并升级数据模型版本。


---

## 17. TMS 应用部署基线（v0.6 修订）

数据库确定后，应用层冻结为：

```text
Browser
  │
  ▼
React SPA
  ├─ Ant Design + Ant Design Table
  └─ Apache ECharts
  │
  ▼ HTTPS / JSON API
FastAPI
  ├─ Identity / Authorization Service
  ├─ Query Service
  ├─ Analytics Service
  ├─ Ingestion Service
  └─ Worker / Job
  │
  ├──────────► NAS / MinIO（原始文件）
  │
  ▼
SQL Server
```

部署原则：

1. 前端为纯 SPA，不引入 SSR/Next.js。
2. 前端不能直连 SQL Server。
3. 所有大数据查询必须经过 FastAPI 做过滤、聚合、分页和权限校验。
4. `measurement` 明细默认禁止无条件全表返回。
5. 文件解析与大型统计任务应由 Worker 执行，API 返回 Job ID/状态，不占用 Web 请求进程长时间计算。
6. 原始文件仍由 NAS/MinIO 保存；数据库只保存结构化结果和血缘。
7. 生产环境前端静态文件可由 Nginx/IIS 提供，FastAPI 独立进程部署。
8. Worker 使用最小权限服务账号；XLSX/CSV/压缩包转换设置超时、内存、临时目录和文件大小限制。
9. Export Artifact 与 Raw File 分开存储和授权，均记录 SHA-256 与保留期限。

### 17.1 UI 技术分工

```text
Ant Design       → 企业后台壳层、表单、抽屉、上传及当前全部业务/工程数据 Table
Apache ECharts   → CP/FT 分析图表与 Wafer 可视化
Optional Wijmo   → 未来进阶版专业 Grid；当前基础版不引入、不产生授权依赖
```

v0.5 当前先以 Ant Design Table 完成 CP/FT 明细、Wafer Summary、PAT/SPC/DQ/Audit 等页面；大数据压力通过 FastAPI 服务端分页/排序/过滤、动态参数列和后端 Export Job 解决。

若未来出现 Excel-like 复制粘贴、成熟转置、Pivot/OLAP 或复杂工程编辑等明确刚需，再通过统一 `EngineeringTable` Adapter 评估 Wijmo/C1，不允许业务页面提前绑定商业 Grid API。
