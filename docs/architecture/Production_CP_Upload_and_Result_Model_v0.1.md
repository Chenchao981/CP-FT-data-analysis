# 量产 CP 上传与清洗结果模型 v0.1

## 业务入口

- 一级业务分类只有“工程数据”和“量产数据”。
- 量产数据下按测试阶段分为 CP 数据、FT 数据；两者保留独立上传和清洗流程。
- CP 首版流程为：登录用户上传源文件 → 自动登记文件台账 → 调用 `F:\cp_data_ansys\packaging\release` → 登记清洗结果 → 进入分析。
- 上传人由认证会话中的 `iam.app_user.user_id` 强制写入。前端和上传接口都不接受人工填写上传人。

## 两层数据结构

### 原始文件台账

沿用 `ingestion.import_batch`、`source_file`、`source_file_receipt`、`import_batch_file`：

- `import_batch.owner_user_id`：登录用户强外键，作为用户数据隔离主键。
- `business_domain=PRODUCTION`、`test_stage=CP`：明确业务和测试阶段。
- `factory_code`：CP分析所需晶圆厂，本版固定华虹选项。
- `source_file`：保存 SHA-256、大小和存储地址；原始文件不写入 SQL 大字段。
- `source_file_receipt`：保留每次上传的原文件名、账号和时间；同一 SHA-256 的重复上传仍保留独立回执。

页面字段：批次编号、SEQ、源文件名称、扩展名、大小、晶圆厂、上传时间、完成时间、上传账号、上传人、状态。

### 清洗结果台账

`ingestion.processing_result_summary` 是列表检索用的处理结果摘要，不替代后续 Canonical 明细：

- 数据名称、产品名称（CP 可空）、Lot、晶圆数、晶圆厂；
- 测试项数、总数、良品数、良率；
- 结果目录、产物 manifest、状态和 Data Type；
- 通过 `import_batch_id`、`job_id` 追溯上传和清洗任务。

测试参数及数值继续以现有 Cleaner 生成的 `cleaned/yield/spec` 标准文件为事实来源，后续由 Canonical Writer 写入 Run/Unit/Test Item/Measurement 模型，不在摘要表重复存储大明细。

## 身份和数据隔离

- 写入：API 从 JWT/会话解析当前用户，服务端覆盖任何客户端身份字段。
- 读取：本人上传的数据可见；拥有有效 `iam.data_scope_grant` 的用户或角色按授权范围可见；系统管理员的 GLOBAL 授权可见全部。
- 人工补录的 `entered_by` 同样由当前登录身份覆盖，不再由页面输入。
