# NCE PYMS（CP/FT 数据管理与良率分析平台）

本仓库建设新洁能产品与良率管理平台。用户可见产品名逐步采用 **NCE PYMS**，数据库、API、环境变量和部署脚本仍保留 `TMS` 技术标识。

## 当前入口

- [2026-09-05 架构与可用性优化验收](docs/development/NCE_PYMS_Architecture_Usability_Completion_Report_2026-09-05.md)：本次完成项、实测性能与未验收边界。
- [CP/FT 阶段合同与扩展指南](docs/architecture/NCE_PYMS_Stage_Contracts_and_Extension_Guide_2026-09-05.md)：当前代码分层和后续物理模型迁移。

- [文档导航](docs/README.md)：现行文档的阅读顺序、冲突优先级和归档说明。
- [产品功能规划 v1.0](docs/product/NCE_PYMS_Functional_Roadmap_v1.0_2026-09-03.md)：2026-09-03 起的产品方向与开发优先级。
- [产品功能边界图](docs/architecture/NCE_PYMS_Product_Function_Boundary_v1.0_2026-09-03.drawio)：正式制造数据平台与个人分析工具的边界。
- [生产部署、备份恢复 Runbook](docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md)：目标环境部署与回滚入口。
- [后端开发说明](backend/README.md) / [前端开发说明](frontend/README.md)。

## 当前产品边界

系统分为两个工作区：

1. **正式制造数据平台**：正式 CP/FT 数据通过已发布 Cleaner 进入唯一 Canonical，形成长期、可比较、可追溯的数据资产。
2. **个人分析工具**：复用既有 CP、FT、VDMOS 工具处理用户选择的目录、文件或压缩包，结果按个人临时制品管理，不自动进入正式数据。

厂家 Parser、单位换算、Bin、Spec 和统计公式继续由已验证的 CP/FT 工具负责；TMS 不对未知格式或缺失业务语义作猜测。

## 交付边界

本仓库中的本机、开发库和灰度记录不等于生产上线。目标服务器、正式账号、HTTPS、备份恢复、持续运行、业务 UAT 和批准签字仍须按生产门禁独立验证。

历史基线、已完成计划和阶段报告已移动到 [docs/archive](docs/archive/README.md)，只作决策演进和交付证据，不再作为默认开发入口。

## 数据安全

仓库禁止提交原始测试数据、客户或产品样例、上传目录、输出报表、数据库、密钥、日志、构建包和本地环境配置。
