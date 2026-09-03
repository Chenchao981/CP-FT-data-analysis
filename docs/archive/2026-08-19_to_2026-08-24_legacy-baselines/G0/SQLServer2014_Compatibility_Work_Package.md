# SQL Server 2014兼容实施工作包

> 对应：G0-DB-04  
> 输入：v0.6的2022+参考草案 `0001 → 0004`  
> 输出：独立、可重复执行的SQL Server 2014 Migration链

## 1. 实施边界

- 不修改或执行2022+参考链；
- 新链使用独立revision ID和SQL目录；
- 目标为SQL Server 2014 SP3、Compatibility Level 120；
- 第一轮只在空白隔离数据库执行；
- 未完成备份、权限和DBA批准前不触碰正式业务数据库。

## 2. 兼容转换清单

| ID | 参考草案 | SQL Server 2014实现 | 验证 |
|---|---|---|---|
| C14-01 | `ISJSON` CHECK | 删除数据库JSON函数约束；应用层JSON Schema校验 | 无效JSON/API拒绝，数据库无未知函数 |
| C14-02 | Measurement CCI | Rowstore；`measurement_id`聚集键候选 | 插入、更新、删除和点查Smoke Test |
| C14-03 | CCI后的普通NCI | 在Rowstore上建立 `unit_id,test_item_id` 等普通NCI | 执行计划使用索引，结果正确 |
| C14-04 | `CREATE OR ALTER VIEW` | 首建使用独立批次 `CREATE VIEW`；后续revision使用 `ALTER VIEW` | 空库升级、重复部署保护 |
| C14-05 | Compatibility假设 | 建库后显式检查/设置Level 120 | `sys.databases.compatibility_level=120` |
| C14-06 | JSON可查询假设 | 高频/受控字段结构化；JSON只存非权威扩展元数据 | 查询不依赖JSON函数 |
| C14-07 | 索引设计 | 根据Lot/Wafer/Unit/Parameter/时间访问路径建立最小NCI | 1M/10M/50M压测与索引使用统计 |
| C14-08 | Driver/TLS | 核对ODBC Driver、Encrypt和证书配置 | Windows 11客户端及应用服务器连通 |
| C14-09 | Edition功能 | 根据实际Edition核对Agent、压缩、在线操作等 | Edition能力矩阵和降级方案 |
| C14-10 | 生命周期风险 | SP3、补丁/ESU、网络隔离、备份恢复 | G0证据和风险Owner |

## 3. Measurement首版候选索引

以下只是压测起点，不直接视为生产定稿：

```sql
CONSTRAINT PK_measurement
    PRIMARY KEY CLUSTERED (measurement_id)

CREATE NONCLUSTERED INDEX IX_measurement_unit_item
ON test.measurement(unit_id, test_item_id)
INCLUDE(value_numeric, value_text, measurement_status, tester_pass_flag);
```

若参数跨Unit分析成为主要负载，再压测候选：

```sql
CREATE NONCLUSTERED INDEX IX_measurement_item_unit
ON test.measurement(test_item_id, unit_id)
INCLUDE(value_numeric, measurement_status);
```

第二个索引会增加装载、日志、存储和维护成本，只有查询收益经证据确认后才创建。

## 4. 独立Migration链建议

```text
2014_0001_core_schema
→ 2014_0002_unified_workflow
→ 2014_0003_governance_seed
→ 2014_0004_analytics_views
→ 2014_0005_constraints_and_indexes
```

拆分原则：Schema、种子、View、索引分revision；失败能够明确定位。自动destructive downgrade继续禁用，回滚依赖已验证备份或批准的forward-fix。

## 5. 自动化验收

### 静态

- 无 `ISJSON`、`OPENJSON`、`JSON_VALUE`；
- 无 `CREATE OR ALTER`；
- Measurement无Columnstore；
- revision单头、无分叉；
- SQL文件按 `GO` 正确拆批。

### 空库

- 从空数据库 `upgrade head`；
- Schema/Table/PK/FK/Index/View/Seed全部存在；
-再次运行不误创建重复对象；
-应用写入合法JSON成功，无效JSON在应用边界失败。

### 业务

- 华虹CP和日月新FT黄金样例完成行、Unit/Die、Measurement、Bin、Yield、Spec和单位对账；
-未知格式、身份、单位、Spec/Bin歧义形成BLOCKER；
-Dataset发布、Evaluation、导出和对象级授权Smoke Test通过。

### 性能

| 规模 | 必测场景 |
|---:|---|
| 1M Measurement | 装载基线、单Unit、单参数、单Lot聚合 |
| 10M Measurement | P95、日志增长、索引大小、并发查询 |
| 50M Measurement | 容量趋势、维护时间、汇总表必要性 |

性能测试必须记录硬件、Edition、数据分布、缓存冷热、执行计划和实际耗时，不能只记录一次最快结果。

## 6. 完成定义

- DBA确认目标实例版本、SP、Edition和补丁；
-兼容链在隔离空库成功升级；
-Rowstore索引方案通过功能与性能测试；
-备份恢复、最小权限和Golden Sample全部通过；
-G0 Gate批准后，2014兼容链替代2022+参考链成为唯一正式入口。

