# G0 阶段执行规划 v0.6

> 计划周期：5～10 个工作日  
> 当前环境：本地 Windows 11 + SSMS；远端 Windows Server 2019 + SQL Server 2014  
> 安全约束：服务器地址、账号、密码和完整盘点结果保存在受控位置，不进入 Git、Markdown、脚本参数或日志。

## 1. G0 结论

现有环境已具备网络和 SSMS 连通条件。数据库目标版本决策已经关闭：首版继续使用 SQL Server 2014。G0当前首要任务是完成实例盘点和独立2014兼容Migration设计，而不是执行现有2022+草案。

批准路线：

1. 建立独立 SQL Server 2014 Compatibility Track；不得直接改写或执行现有 `0001 → 0004` 2022+草案。
2. Measurement采用Rowstore + 普通非聚集索引，首版不建Clustered Columnstore。
3. JSON由应用层严格校验；关键业务字段保持结构化。
4. **禁止**：边执行边手工删约束/索引，或把 `sa` 作为应用账号。

SQL Server 2014 的常规扩展支持已经结束。继续使用的风险已经进入G0治理范围，必须确认SP3/最新适用安全更新、ESU状态、备份恢复和隔离措施。

## 2. 已确认事实与待验证项

### 已确认

- 客户端为 Windows 11，SSMS 已安装；
- 远端操作系统为 Windows Server 2019；
- 数据库为 SQL Server 2014；
- 当前账号可以通过 SSMS 联通实例。
- 实测版本为12.0.5000.0 SP2 Enterprise，Collation为Chinese_PRC_CI_AS；
- 隔离数据库 `TMS_G0_DEV` 已升级到 `sql2014_0006` 并通过Schema验证；CP Product可空、FT Lot可空，Stage必需身份由数据库约束，人工补录/忽略决定独立留痕。

### 必须现场验证

- SP3升级后的 Product Version、Product Level；
- 当前补丁/ESU 状态；
- 实例 Collation、数据库 Compatibility Level、Recovery Model；
- CPU、内存、磁盘余量、数据/日志/备份盘分布；
- 是否已有业务数据库、命名冲突、维护窗口和可用备份；
- 连接是否加密、SQL Server Browser/端口暴露、审计和登录策略；
- SQL Server 2014兼容Migration的隔离测试库、发布窗口和回退方式。

## 3. 已确认的 SQL Server 2014 兼容缺口

| 缺口 | 当前 v0.6 用法 | SQL Server 2014 处理影响 |
|---|---|---|
| JSON 内置函数 | 多处 `ISJSON(...)` CHECK | 2014 无该函数；需应用层校验或改用结构化子表 |
| View DDL | `CREATE OR ALTER VIEW` | 2014 不支持；需分批 `CREATE`/`ALTER` 或条件动态 SQL |
| Columnstore + B-tree | Measurement 建 CCI 后再建普通非聚集索引 | 2014支持非聚集索引，但CCI表不能再建普通B-tree索引；已决定改用Rowstore + 普通非聚集索引 |
| 生命周期 | v0.6 假设受支持的新版本 | 2014 已结束常规扩展支持，需要升级或批准 ESU/隔离风险 |

Compatibility Track的Measurement使用Rowstore + 必要B-tree索引。完成1M/10M/50M数据压测后，再决定是否增加独立汇总/分析表；不在首版Measurement上使用CCI。

## 4. G0 工作包

| ID | 任务 | 建议责任角色 | 工期 | 前置 | 交付证据 |
|---|---|---|---:|---|---|
| G0-DB-01 | 只读实例盘点 | DBA/IT | 已完成 | 已连通 | SP2 Enterprise、版本/Edition/Collation/数据库清单 |
| G0-DB-02 | 补丁与生命周期核查 | DBA/IT安全 | 0.5 天 | DB-01 | SP3/补丁/ESU状态、风险说明 |
| G0-DB-03 | SQL Server 2014目标版本ADR | CIO/IT/DBA/架构 | 已完成 | 用户决定 | ADR-0001、Rowstore/JSON/View边界 |
| G0-DB-04 | SQL Server 2014兼容Migration | 架构/开发/DBA | 已完成首版 | DB-01/03 | `sql2014_0001 → 0004`，空库升级和Schema验证PASS |
| G0-SEC-01 | 凭据与最小权限设计 | IT安全/DBA | 0.5 天 | DB-03 | Migration/Runtime/ReadOnly 三类账号权限矩阵 |
| G0-SEC-02 | 网络与连接安全检查 | IT安全/DBA | 0.5 天 | DB-01 | 端口、来源范围、TLS、审计、失败登录策略 |
| G0-OPS-01 | 备份与恢复门禁 | DBA/运维 | 1 天 | DB-03 | Full/Diff/Log 策略、保留期、一次 Restore Drill 计划 |
| G0-DATA-01 | 华虹 CP 黄金样例台账 | CP业务/数据工程 | 1～2 天 | 受控样例区 | SHA、格式、行/Die、Wafer、Bin、Yield、Spec 对账清单 |
| G0-DATA-02 | 日月新 FT 黄金样例台账 | FT业务/数据工程 | 1～2 天 | 受控样例区 | SHA、Unit、参数、单位、Result、Bin、Retest 对账清单 |
| G0-RULE-01 | Spec/Bin/Retest 口径会 | CP/FT/质量/研发 | 0.5～1 天 | DATA-01/02 | 决策记录、冲突样例、Owner |
| G0-RULE-02 | PAT/SBL/Cpk/SPC Owner 与版本策略 | 质量/工艺/研发 | 0.5 天 | RULE-01 | 算法Owner、批准/生效/废止流程 |
| G0-STOR-01 | 原始文件与导出保留策略 | IT/质量/业务 | 0.5 天 | 合规输入 | NAS路径规范、权限、期限、删除审批和恢复要求 |
| G0-ARCH-01 | P0 接口合同冻结 | 架构/开发/测试 | 1 天 | DB-03/RULE-01 | Detector、Cleaner、DQ、Dataset、Evaluation、Export 合同 |
| G0-GATE-01 | G0 Gate Review | CIO/项目组 | 0.5 天 | 全部强制项 | 通过/有条件通过/不通过结论及P0 Backlog |

`DB-03`和`DB-04`已关闭；当前数据库环境门禁为SP3升级及升级后复验。

`DB-04`的具体转换、revision拆分和测试矩阵见 [`SQLServer2014_Compatibility_Work_Package.md`](SQLServer2014_Compatibility_Work_Package.md)。

## 5. 安全执行要求

1. 不在应用中使用 `sa`；G0 连通验证结束后创建专用 Migration、Runtime 和 ReadOnly 身份。
2. 已通过聊天或人工渠道传递的高权限密码应在验证后轮换，并进入公司批准的凭据保管机制。
3. 测试命令不得把密码写入命令行、PowerShell 历史、`.env`、截图或输出文件。
4. 盘点SQL只读执行；未完成备份和2014兼容Migration评审前，不创建正式数据库、不改实例配置、不启停服务。
5. 原始样例和盘点结果放在 `evidence/private/` 或受控共享区；该目录已被 Git 忽略。

## 6. 已批准路线的最低条件

- SQL Server 2014 至少为 SP3，并核实最新适用安全更新/ESU；
- 独立 Compatibility Migration 通过空库和升级测试；
- JSON 校验转移、Rowstore索引、View DDL替代方案有自动化测试；
- 1M/10M/50M Measurement完成装载、钻取和聚合压测；
- 业务Owner接受生命周期、性能和长期技术债风险。

## 7. G0 Gate 通过标准

- 数据库目标版本 ADR 已批准；
- 实例、补丁、Edition、Collation、Compatibility、容量和备份证据齐全；
- 高权限共享账号不作为应用账号，最小权限矩阵已批准；
- 华虹 CP、日月新 FT 黄金样例及预期结果已批准，真实数据未进入 Git；
- Spec、Bin、Retest、单位及未知格式的阻断规则有明确Owner；
- 原始文件、Dataset、评价结果、导出物的存储和保留策略确定；
- P0 接口、测试矩阵、风险台账和首批 Backlog 已评审；
- 未关闭项都有Owner、截止日期，且没有数据库版本或数据正确性 BLOCKER。

## 8. 推荐日程

```text
Day 1  DB-01 / DB-02 / SEC-02
Day 2  DB-03 ADR归档 + DB-04兼容设计
Day 3  DB-04 + SEC-01 + OPS-01
Day 1-4 DATA-01 / DATA-02 并行建立黄金样例
Day 4-5 RULE-01 / RULE-02 / STOR-01
Day 6-8 ARCH-01、测试矩阵与风险关闭
Day 9-10 G0-GATE-01；通过后启动 P0
```

P0数据库实现必须等待SQL Server 2014兼容链在隔离DEV/TEST数据库完成空库验证；不得把现有正式业务库当作临时开发库。

## 9. 官方兼容依据

- [SQL Server 2014 生命周期](https://learn.microsoft.com/en-us/lifecycle/products/sql-server-2014)
- [SQL Server Extended Security Updates FAQ](https://learn.microsoft.com/en-us/lifecycle/faq/sql-server-extended-security-updates)
- [Windows 与 SQL Server 版本要求](https://learn.microsoft.com/en-us/troubleshoot/sql/database-engine/install/windows/use-sql-server-in-windows)
- [SQL Server Compatibility Level](https://learn.microsoft.com/en-us/sql/t-sql/statements/alter-database-transact-sql-compatibility-level)
- [SQL Server JSON 函数适用版本](https://learn.microsoft.com/en-us/sql/relational-databases/json/validate-query-and-change-json-data-with-built-in-functions-sql-server)
- [SQL Server 2014 Columnstore 限制](https://learn.microsoft.com/en-us/sql/relational-databases/indexes/columnstore-indexes-what-s-new)
