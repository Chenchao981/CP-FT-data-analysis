# ADR-0001：TMS 首版继续使用 SQL Server 2014

> 状态：Accepted  
> 决策日期：2026-08-20  
> 决策范围：G0、P0 和首版生产数据库物理实现

## 背景

现有可用环境为 Windows Server 2019 + SQL Server 2014，本地 Windows 11 已通过 SSMS 验证连通。原 v0.6 数据库草案按 SQL Server 2022+ 编写，存在 `ISJSON`、`CREATE OR ALTER VIEW` 和 SQL Server 2014 Columnstore 组合限制。

SQL Server 2014 支持 Rowstore 表、普通聚集索引、普通非聚集索引和过滤索引。需要规避的不是“非聚集索引”，而是 SQL Server 2014 **在同一张表使用 Clustered Columnstore Index 后不能再创建普通 B-tree 非聚集索引**。

## 决策

1. TMS 首版继续使用 SQL Server 2014，数据库 Compatibility Level 目标为 `120`。
2. SQL Server 2014 必须核实为 SP3 及适用的最新安全更新；结果纳入 G0 证据。
3. `test.measurement` 首版采用 **Rowstore**：
   - `measurement_id` 作为聚集主键或经压测批准的聚集键；
   - 建立 `unit_id + test_item_id` 等必要普通非聚集索引；
   - 不在该表建立 Clustered Columnstore Index。
4. JSON 字段继续用 `nvarchar(max)` 保存，但数据库不使用 `ISJSON`；由 API/应用层做严格 JSON Schema 校验，关键业务字段必须结构化建列/建表。
5. View 使用 SQL Server 2014兼容的 `CREATE VIEW` / `ALTER VIEW` 独立批次，不使用 `CREATE OR ALTER`。
6. 建立独立的 SQL Server 2014 Migration 链；现有 `0001 → 0004` 2022+草案保留为参考，在兼容链验收前禁止执行。
7. 应用不得使用 `sa`；使用独立 Migration、Runtime、ReadOnly账号和最小权限。

## 影响

### 正面

- 复用现有服务器、SSMS和运维环境；
- Unit/Parameter钻取可以使用成熟的B-tree索引；
- 不引入数据库升级作为P0前置条件。

### 代价与风险

- JSON合法性不能靠数据库内置函数保证；
- Measurement大规模聚合性能必须依赖索引、汇总表、分批查询和真实压测；
- SQL Server 2014已结束常规扩展支持，补丁/ESU、网络隔离、备份恢复和技术债需要持续管理；
- 未来升级数据库时，需要单独评估Columnstore迁移，不能仅靠修改Compatibility Level完成。

## 验收条件

- SQL Server 2014 SP/补丁、Edition、Collation、Compatibility、容量和备份核实完成；
- SQL Server 2014兼容Migration在空库完成 `upgrade head`；
- Rowstore Measurement在1M/10M/50M行完成装载、Unit钻取、参数筛选和聚合压测；
- 应用层JSON Schema校验包含有效、无效、超大和恶意输入测试；
- 权限、备份恢复和黄金样例对账通过。

