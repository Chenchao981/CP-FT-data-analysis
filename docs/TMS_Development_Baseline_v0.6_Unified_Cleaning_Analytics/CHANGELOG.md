# CHANGELOG

## v0.6 — Unified Cleaning Result and Analytics Baseline

### Added

- 基于 `cp_data_ansys`、`data_IGBT_multiple` 和 `VDMOS_Tool_v8.9.html` 的能力映射与采用/禁用边界。
- Input Set、多文件/多角色输入、Format Profile 与 Cleaner Release。
- Dataset / Dataset Version / Dataset Version Run 正式发布模型。
- DQ Rule Version，明确 BLOCKER 不可 Waive。
- Evaluation Rule Version / Evaluation Run / Metric Result，覆盖 Spec/PAT/SBL/CPK/SPC。
- IAM、RBAC、Data Scope、Saved Analysis、Export Job 和 Export Artifact。
- 按 Overview/Detail/Parameter/CP Spatial/Quality/Delivery 组织图表，并冻结 Lot/Wafer/Parameter 多选合同。
- 可执行 Alembic `0001→0004` revision 链、种子数据和 Current Published Views。

### Changed

- 用户主线升级为 Input Set → Dataset Version → Evaluation/Export。
- Final Yield 排序改为 `(run_attempt_no, attempt_no, source_sequence)` 或显式版本化 disposition。
- API 区分 Processing Run、Dataset Version、Evaluation Run 和 Export Job ID。
- 当前页导出也走后端授权与审计。
- 厂家兼容输出与内部 Canonical Model 分离。

### Explicitly Not Adopted from References

- 硬编码 `BIN=1`；
- 图表层静默 IQR 删除或单位转换；
- 找不到规格时合并全部产品规格或取第一份；
- 浏览器端形成第二套清洗/统计权威；
- Local Storage 作为正式 Dataset 或 Saved Analysis 存储。

### Validation Boundary

- 两个 Python 项目完成当前文档/注册/实现路径核对；HTML 完成源码级能力与公式检查。
- 浏览器安全策略阻止本地 `file://` 渲染，本版不声称 HTML 已完成视觉验收。
- Alembic/Python 可做静态检查；SQL Server `upgrade head` 仍需测试实例验收。

---

## v0.5 — Free Frontend Baseline

### Changed

- 默认工程 Grid 从 Wijmo FlexGrid 切换为 **Ant Design Table**。
- 当前基础版前端栈冻结为 `React + TypeScript + Vite + Ant Design + ECharts + TanStack Query + Zustand`。
- CP Die、FT Unit、Measurement、Wafer Summary、PAT/SPC/DQ/Audit 明细第一阶段统一使用 Ant Design Table。
- 新增统一 `EngineeringTable` 抽象，集中处理服务端分页、排序、过滤、动态列、固定列、导出触发和格式化。
- Wide View 推荐由 FastAPI 后端按选定参数动态组装/Pivot，浏览器不从海量 Long Measurement 自行 Pivot。
- 大结果集 Excel/CSV 导出改为后端 Export Job；当前页小数据可前端快速导出。

### Optional / Deferred

- Wijmo / ComponentOne FlexGrid 从正式依赖降级为 **可选进阶版**。
- 只有 Excel-like 复制粘贴、成熟转置表、Pivot/OLAP、高级编辑等需求达到明确 ROI 后才引入。
- 基础版业务页面不得直接依赖 Wijmo API，未来通过 `EngineeringTable` Adapter 升级。

### Unchanged from v0.4

- SQL Server Canonical Model。
- Measurement / Evaluation 分层。
- Spec/Bin Resolution 与 Scope Priority。
- Processing Job / Run、DQ、Audit、Parser rerun。
- Retest、重复上传、时区冻结规则。
- Alembic + Native T-SQL migration 策略。

---

## v0.4 — Production Data Governance Baseline

### Added

- `measurement_evaluation`：Spec/PAT/SBL 等规则判断与 Measurement Fact 分离。
- `spec_binding` + `scope_priority`：定义 Spec 唯一匹配优先级。
- `bin_mapping_set` + `unit_bin_evaluation`：Raw Bin 与业务 Failure Mode 分离并可追溯。
- `source_file_receipt`：同一内容多次上传不重复建事实。
- `processing_job` / `processing_run`：Parser 执行、重跑、版本、current/superseded 血缘。
- `data_quality_rule` / `data_quality_issue`：数据质量 Gate 与问题明细。
- `governance.audit_log`：规则/人工治理动作审计。
- UTC + source local/timezone 双时间模型。
- Alembic + Native T-SQL migration 基线。

### Changed

- `measurement.is_in_spec` 从 Canonical Fact 中移除。
- `measurement.is_pass` 更名/收敛为 `tester_pass_flag`，只表示源 Tester 判断。
- `source_file` 从“上传/解析状态混合表”改成内容身份表；上传事件拆为 receipt。
- Parser Profile 版本不可变，唯一键从 format 改为 `(format_code, parser_version)`。
- Retest 由 `run_attempt_no` + `logical_unit_key/attempt_no` 明确区分 Run/Unit 两级。
- 默认 analytics 只读 current + published processing run。

### Frozen Decisions

- 重复 SHA256 默认不重新生成 canonical data。
- Force Reprocess 产生新 Processing Run，不复制 Source File。
- Parser 重跑不 UPDATE 旧 measurement。
- Scope 最高优先级多条匹配必须报 `CONFIG_AMBIGUOUS`。
- 所有系统时间 UTC，禁止隐式采用服务器本地时区。
- Markdown DDL 不再是部署来源。
