# NCE PYMS 文档导航

更新时间：2026-09-05

本目录只在主区保留仍用于产品决策、开发约束、格式合同、部署运维和安全收口的文档。旧版整套基线、已完成计划和阶段交付报告统一放入 [`archive/`](archive/README.md)。归档表示“不再作为默认执行依据”，不表示内容被删除或历史证据无效。

## 1. 先读这两份

- [CP/FT 物理分表完成报告](development/NCE_PYMS_Physical_Stage_Storage_Completion_Report_2026-09-05.md)：当前 sql2014_0028、全量历史对账及真实入库验证。
- [CP/FT 物理存储合同](architecture/NCE_PYMS_Physical_Stage_Storage_2026-09-05.md)：阶段事实表、全局 ID、评价外键和容量要求。
- [数据库结构与字段整改验收](development/NCE_PYMS_Database_Stage_Fields_Completion_Report_2026-09-05.md)：sql2014_0027、迁移对账及验证边界。
- [数据库阶段字段字典](architecture/NCE_PYMS_Database_Stage_Fields_2026-09-05.md)：CP/FT 运行身份、来源和规格字段。
- [2026-09-05 架构与可用性优化验收](development/NCE_PYMS_Architecture_Usability_Completion_Report_2026-09-05.md)：本次完成项、实测性能与未验收边界。
- [CP/FT 阶段合同与扩展指南](architecture/NCE_PYMS_Stage_Contracts_and_Extension_Guide_2026-09-05.md)：当前代码分层和新厂家扩展。

1. [NCE PYMS 功能规划与现状对齐 v1.0](product/NCE_PYMS_Functional_Roadmap_v1.0_2026-09-03.md)：当前产品定位、双工作区边界和 P0～P3 优先级。
2. [NCE PYMS 产品功能边界图](architecture/NCE_PYMS_Product_Function_Boundary_v1.0_2026-09-03.drawio)：正式制造数据平台、个人分析工具及后续扩展的视觉边界。

## 2. 业务规则阅读顺序

这些文档是逐层修订关系，不应只读其中一份。发生冲突时，以日期更晚、范围更具体的文档为准：

1. [TMS 业务需求规格 v0.2](business/TMS_Business_Requirements_v0.2.md)：Cleaner 权威、正式入库、Canonical、Lot、Spec、导出与重清洗等基础规则。
2. [Lot 输入恢复架构](architecture/TMS_Lot_Input_Recovery_Architecture_v0.1_2026-08-27.md)：替代 v0.2 中已经失效的缺 Lot 后置补录规则。
3. [TMS 业务需求 v0.3](business/TMS_Business_Requirements_v0.3.md)：补充重复上传和 Dataset 独立性；其中旧的工程/量产可见性规则已被后续文档替代。
4. [数据归属与本机快速分析需求 v1.0](business/TMS_Data_Ownership_and_Local_Analysis_Requirements_v1.0_2026-09-01.md)：定义 PERSONAL、DOMAIN 和 Local Agent 边界。
5. [开发期权限与用户入口需求](business/TMS_Development_First_Access_and_Entry_Requirements_2026-09-02.md)：当前开发期管理员访问口径、CP/FT 两入口以及 SAP 判定未完成时的技术占位规则。
6. [NCE PYMS 功能规划 v1.0](product/NCE_PYMS_Functional_Roadmap_v1.0_2026-09-03.md)：最新产品导航和正式数据/个人工具分层。

补充业务资料：

- [CP/FT 分析业务事实](business/CP_FT_Analysis_Business_Facts_v0.1.md)
- [能力分类基线](business/TMS_Capability_Classification_v1.0_2026-08-27.md)
- [快速分析业务需求](business/TMS_Quick_Analysis_Business_Requirements_v0.1.md)
- [新洁能业务对齐](business/TMS_NCEpower_Business_Alignment_2026-08-30.md)

## 3. 当前架构与合同

- [Route A 正式入库架构](architecture/TMS_System_Architecture_v0.7_Route_A.md)：正式数据唯一 Canonical 主链。
- [正式数据与快速分析双通道架构](architecture/TMS_System_Architecture_v0.8_Dual_Channel.md)：正式数据和临时 Workspace 的隔离边界。
- [数据访问与 Local Agent 架构](architecture/TMS_Data_Access_and_Local_Agent_Architecture_v1.0_2026-09-01.md)：PERSONAL/DOMAIN、服务器目录和用户电脑本地计算边界。
- [CP/FT 独立接入与人工补录](architecture/CP_FT_Separate_Ingestion_and_Manual_Enrichment_v0.1.md)
- [SAP-B1 / MES / QMS 接口合同清单](architecture/TMS_SAP_MES_QMS_Interface_Contract_Checklist_v1.0_2026-08-29.md)
- [SQL Server 2014 决策记录](adr/ADR-0001_SQLServer2014_Target.md)

## 4. 开发、格式与算法参考

- [现有 Cleaner 输出合同](formats/Existing_Cleaner_Output_Contract_2026-08-21.md)
- [华虹格式合同与证据](formats/huahong/README.md)
- [Analytics Golden Source Manifest](development/TMS_Analytics_Golden_Source_Manifest_2026-08-31.md)
- [VDMOS 参考算法审计](development/TMS_VDMOS_Reference_Algorithm_Audit_2026-08-30.md)

## 5. 部署、运维与安全

- [Windows Runtime 部署指南](development/TMS_Windows_Runtime_Deployment_Guide.md)
- [生产部署、备份恢复与回滚 Runbook](operations/TMS_Production_Deployment_Backup_Restore_Runbook.md)
- [开发期延期安全项](security/TMS_Deferred_Security_Backlog_2026-09-02.md)
- [生产运行配置示例](examples/TMS.production.runtime.example.ps1)

## 6. 文档维护规则

- 新需求先修改现行主文档或增加明确的增量变更单，不再复制整套基线目录。
- 每份增量文档必须写清“继承什么、替代什么、哪些内容不变”。
- 阶段计划完成后移入 `archive/...completed-plans/`；完成报告、回归报告和灰度报告移入 `archive/...delivery-records/`。
- 归档文档只用于追溯；恢复为现行依据前，必须重新核对代码、数据库、部署环境和更晚的业务决策。
