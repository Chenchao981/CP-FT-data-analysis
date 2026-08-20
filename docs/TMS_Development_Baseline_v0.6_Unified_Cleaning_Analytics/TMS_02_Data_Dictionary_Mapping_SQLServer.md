# TMS v0.6 数据字典与字段映射（SQL Server）

> 本文以 v0.4 Canonical Fact 为事实层基础，并补充 v0.6 的 Input Set、Dataset Version、Rule Version、Evaluation Run、IAM 与 Export 实体。厂家原始字段只能经批准的 Format Profile 和 Cleaner Release 映射，不得由图表层临时解释。

## 1. 时间与类型约定

| 语义 | 类型 | 约定 |
|---|---|---|
| 主键 | `bigint IDENTITY` | 内部 surrogate key |
| 测量值 | `float(53)` | 科学计数法/跨度大 |
| 系统时间 | `datetime2(3)` | **UTC**，字段名以 `_utc` 结尾 |
| 源本地时间 | `datetime2(3)` | 必须配 `source_timezone_iana` |
| 时区 | `nvarchar(64)` | IANA，例如 `Asia/Shanghai` |
| JSON | `nvarchar(max)` | `ISJSON` 校验 |
| 状态 | `varchar(n)` | CHECK 约束或受控字典 |

## 2. 核心状态码

### processing_run.status

```text
CREATED
PARSING
NORMALIZING
VALIDATING
READY
PUBLISHED
FAILED
SUPERSEDED
CANCELLED
```

### data_quality_issue.severity

```text
INFO
WARNING
ERROR
BLOCKER
```

### evaluation_result

```text
PASS
FAIL
NOT_EVALUATED
NO_MATCH
CONFIG_AMBIGUOUS
INVALID_VALUE
```

### measurement_status

```text
MEASURED
OVER_RANGE
UNDER_RANGE
NOT_TESTED
MISSING
INVALID
NOT_APPLICABLE
```

> `PASS/FAIL` 不再混在 measurement status 中；Tester 自带判断使用 `tester_pass_flag`，业务 Spec 判断使用 `measurement_evaluation`。

---

## 3. mdm.supplier

新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| default_timezone_iana | nvarchar(64) | 厂商/工厂默认时区，例如 `Asia/Shanghai` |

华虹、日月新当前文件若不含明确时区，可由 supplier 默认时区解析，但必须写入 `timezone_resolution=SUPPLIER_DEFAULT`。

---

## 4. mdm.scope_priority

定义 Spec/Bin 规则匹配优先级。

| 字段 | 类型 | 说明 |
|---|---|---|
| scope_code | varchar(64) | PK |
| priority | int | 数值越大优先级越高 |
| description | nvarchar(300) | 解释 |
| active | bit | 是否启用 |

v0.4 初始优先级：

| scope_code | priority |
|---|---:|
| EXPLICIT_OVERRIDE | 600 |
| CUSTOMER_PRODUCT_PROGRAM | 500 |
| CUSTOMER_PRODUCT | 450 |
| PRODUCT_PROGRAM | 400 |
| PRODUCT_SUPPLIER_STAGE | 350 |
| PRODUCT_STAGE | 300 |
| PRODUCT | 200 |
| GLOBAL | 100 |

同一匹配上下文如果最高优先级存在两条 binding，Resolver 必须返回 `CONFIG_AMBIGUOUS`。

---

## 5. mdm.spec_set / mdm.spec_item

仍表示版本化 Spec Definition。

### spec_set

关键字段：

```text
spec_set_id
product_id
spec_name
version_code
status
source_type
source_ref
effective_from_utc
effective_to_utc
```

### spec_item

关键字段：

```text
spec_item_id
spec_set_id
test_item_id / canonical_parameter_code
lsl
usl
target_value
operator
unit
raw_spec
condition_json
```

---

## 6. mdm.spec_binding

表示 Spec 的适用范围，而不是规格数值本身。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| spec_binding_id | bigint | Y | PK |
| spec_set_id | bigint | Y | 命中的 Spec Set |
| scope_code | varchar(64) | Y | 关联 scope priority |
| supplier_id | bigint | N | 限定测试方 |
| product_id | bigint | N | 限定产品 |
| test_stage | varchar(16) | N | CP/FT |
| program_version_id | bigint | N | 限定程序版本 |
| customer_code | nvarchar(128) | N | 客户规格 |
| quality_grade | nvarchar(64) | N | INDUSTRIAL/AUTOMOTIVE 等 |
| package_code | nvarchar(64) | N | 封装范围 |
| effective_from_utc | datetime2(3) | N | 生效 |
| effective_to_utc | datetime2(3) | N | 失效 |
| active | bit | Y | 是否启用 |

Resolver 只匹配 Binding 中**非空限定字段**。

---

## 7. mdm.bin_mapping_set / mdm.bin_definition

### bin_mapping_set

用于版本化一套 Bin 解释规则。

```text
bin_mapping_set_id
scope_code
supplier_id
product_id
test_stage
program_version_id
version_code
effective_from_utc/effective_to_utc
active
```

### bin_definition

```text
bin_mapping_set_id
bin_type
bin_code
bin_name
failure_mode
is_pass
severity
```

原始 Bin 永远保留在 `unit_result`，业务解释写入 `unit_bin_evaluation`。

---

## 8. ingestion.source_file

**内容级唯一实体。**

| 字段 | 说明 |
|---|---|
| source_file_id | PK |
| sha256 | 内容 Hash，非空时唯一 |
| file_size | 原始字节数 |
| canonical_storage_uri | NAS/MinIO 路径 |
| first_seen_utc | 第一次发现时间 |

不再保存“当前 Parser 版本/当前 parse status”——这些属于 `processing_run`。

---

## 9. ingestion.source_file_receipt

每一次上传/接收行为一条。

```text
receipt_id
source_file_id
import_batch_id
original_file_name
received_by
received_channel
received_at_utc
is_duplicate_receipt
```

重复 SHA256：创建 receipt，但默认不创建新的 canonical facts。

---

## 10. ingestion.parser_profile

一行代表一个**不可变 Parser Version**：

```text
format_code
parser_name
parser_version
code_checksum
canonical_model_version
active/is_default
```

唯一键建议：

```text
format_code + parser_version
```

升级 Parser = 新增版本，不 UPDATE 旧版本号。

---

## 11. ingestion.processing_job

表示用户/系统发起的一次处理任务。

| 字段 | 说明 |
|---|---|
| job_id | PK |
| source_file_id | 文件 |
| job_type | PARSE / REPROCESS / REEVALUATE / OTHER |
| trigger_type | MANUAL / AUTO / API / SCHEDULED |
| requested_by | 请求人/服务账号 |
| parent_job_id | 重试/派生任务 |
| status | QUEUED/RUNNING/SUCCESS/FAILED/CANCELLED |
| requested_at_utc | UTC |
| started_at_utc | UTC |
| finished_at_utc | UTC |
| reason | 强制重跑原因 |

---

## 12. ingestion.processing_run

**Parser/Normalizer 的一次不可变执行尝试。**

```text
processing_run_id
job_id
source_file_id
parser_profile_id
parser_version
canonical_model_version
status
is_current
supersedes_processing_run_id
row_count_input
unit_count_output
measurement_count_output
dq_warning_count
dq_error_count
started_at_utc
finished_at_utc
```

一个 source file 最多只能有一个 `is_current=1 AND status='PUBLISHED'` 的 run。

---

## 13. ingestion.data_quality_rule

```text
rule_code
rule_name
default_severity
is_blocking
applies_stage
description
active
```

示例：

```text
DQ_DUPLICATE_CP_COORDINATE
DQ_MISSING_BIN
DQ_UNKNOWN_TIMEZONE
DQ_SPEC_AMBIGUOUS
DQ_INVALID_NUMERIC_VALUE
DQ_FT_MISSING_STABLE_UNIT_ID
```

---

## 14. ingestion.data_quality_issue

每一个实际问题一条。

```text
processing_run_id
rule_id
severity
entity_type
entity_key
source_row_no
source_column
raw_value
message
resolution_status
resolved_by
resolved_at_utc
```

`BLOCKER` 或 blocking rule 未解决时 processing run 不得 PUBLISHED。

---

## 15. test.test_run

v0.4 时间字段：

```text
started_at_utc
ended_at_utc
source_started_local
source_ended_local
source_timezone_iana
source_utc_offset_minutes
timezone_resolution
```

血缘字段：

```text
processing_run_id
```

Retest：

```text
run_attempt_no
```

---

## 16. test.unit_result

CP：一条 = Die 的一次测试 attempt。  
FT：一条 = Unit 的一次测试 attempt。

新增/冻结：

```text
logical_unit_key
attempt_no
```

华虹 CP：

```text
logical_unit_key = CP|Product|Lot|Wafer|X|Y
```

日月新 FT：若没有稳定 Serial，则先以厂商 Unit/Test No. 组合，但记录 DQ Warning，并等待更多样本确定长期 identity。

---

## 17. test.measurement

v0.4 事实字段：

| 字段 | 说明 |
|---|---|
| measurement_id | PK |
| unit_id | Unit attempt |
| test_item_id | Test Step |
| value_numeric | 数值 |
| value_text | 文本值 |
| raw_value | 原始字符串 |
| measurement_status | MEASURED/OVER_RANGE/... |
| tester_pass_flag | Tester 原始 PASS/FAIL，若源文件提供 |
| source_column_index | 原列 |

### 明确删除

```text
is_in_spec
```

业务规格判断进入 `measurement_evaluation`。

---

## 18. test.measurement_evaluation

| 字段 | 说明 |
|---|---|
| evaluation_id | PK |
| measurement_id | 被评价事实 |
| evaluation_type | SPEC/PAT/SBL/SAFE_LAUNCH |
| evaluation_scope_key | DEFAULT/CUSTOMER:xx 等 |
| spec_binding_id | 实际命中的 Binding，可空 |
| spec_item_id | 实际命中的 Spec Item，可空 |
| lsl_applied/usl_applied | 实际应用值快照 |
| lower/upper_operator_applied | 运算符快照 |
| evaluation_result | PASS/FAIL/NO_MATCH/CONFIG_AMBIGUOUS/... |
| evaluation_reason | 解释 |
| processing_run_id | 哪次计算产生 |
| is_current | 当前有效评价 |
| evaluated_at_utc | UTC |

同一 measurement + evaluation_type + scope_key 只能有一条 current evaluation。

---

## 19. test.unit_bin_evaluation

记录原始 Bin 如何映射成业务语义：

```text
unit_id
bin_type
raw_bin_code
bin_mapping_set_id
bin_definition_id
mapping_status
is_pass_snapshot
failure_mode_snapshot
processing_run_id
```

---

## 20. governance.audit_log

只记录配置/规则/主数据/人工操作，不为每条 measurement 写审计。

```text
audit_id
actor
operation
entity_type
entity_id
before_json
after_json
reason
correlation_id
occurred_at_utc
```

重点审计：

```text
Spec 发布/废止
Spec Binding 变更
Bin Mapping 变更
Scope Priority 变更
Parser 默认版本切换
DQ Issue 人工豁免
Force Reprocess
```

---

## 21. 华虹 CP / 日月新 FT 映射变化

源字段映射本身不改变，变化在“谁负责判断”：

### 华虹

```text
X/Y/Bin/Measurement → 事实表
LimitU/LimitL       → Test Program Embedded Limit 或受控 Spec 候选
后续未测             → measurement_status=NOT_TESTED
```

### 日月新

```text
Soft Bin / Hard Bin → unit_result 原始字段
T11 ISGS = OVER     → raw_value='OVER', measurement_status=OVER_RANGE
后续 T12...         → NOT_TESTED
Low/High Limit      → Program Limit / Spec Source，不直接覆盖受控 Spec
```

**Parser 不负责偷偷决定“最终公司 Spec”。Parser 负责忠实解析；Spec Resolver 负责规则匹配。**

## 22. v0.6 iam.app_user / role / permission

`app_user` 保存稳定用户主键、登录名、显示名、Identity Provider、外部 Subject、状态及可空的本地密码哈希。`role` 与 `permission` 多对多，`user_role` 负责用户授予；`data_scope_grant` 限定 GLOBAL/DEPARTMENT/PROJECT/PRODUCT/SUPPLIER/OWNER 等数据范围。管理员角色不自动等于文件下载权限。

## 23. ingestion.import_batch_file

| 字段 | 必填 | 说明 |
|---|---:|---|
| import_batch_file_id | Y | PK |
| import_batch_id | Y | Input Set |
| receipt_id | Y | 具体接收事件 |
| file_role | Y | DETAIL/YIELD/SPEC/PAT/EXPORT/REPORT/MANIFEST/OTHER |
| ordinal_no | Y | 用户选择/业务解析顺序 |
| required_flag | Y | 是否必需 |
| detected_format_code/version | N | 检测快照，不替代批准档案 |

`processing_job.import_batch_id` 是 v0.6 正式输入；兼容的 `source_file_id` 仅用于单文件历史任务。

## 24. ingestion.format_profile / cleaner_release

Format Profile 与 Cleaner Release 分开版本化：

```text
format_profile
  supplier / stage / format_code / profile_version
  signature_json / file_role_contract_json / status

cleaner_release
  cleaner_code / cleaner_version / code_checksum / artifact_uri
  supported_profile_id / status / approved_by / approved_at_utc
```

Profile 描述“批准了什么输入结构”，Cleaner Release 描述“哪份代码处理它”。两者都不可静默替换。

与历史 `parser_profile` 的边界：`format_profile` 是业务批准的输入契约，`cleaner_release` 是可部署代码制品，`parser_profile` 是一次 Canonical 解析实现的执行身份。Processing Run 必须能同时追溯到输入契约和实际执行制品；三者不能用一个自由文本版本号代替。

## 25. dataset.dataset / dataset_version / dataset_version_run

`dataset` 是稳定业务对象，保存 Dataset Type、Stage、Supplier、Product、Project 和拥有者。`dataset_version` 保存业务版本、状态、current 标志、Input Set、Canonical Model Version、发布对账计数及发布审计。`dataset_version_run` 允许一个 Dataset Version 关联一个或多个 Processing Run，并记录角色。

## 26. ingestion.dq_rule_set / dq_rule_version

`data_quality_rule` 只保存稳定 `rule_code`；执行语义进入不可变 `dq_rule_version`：severity、is_blocking、waivable、implementation_version、parameters_json。`data_quality_issue` 保存实际命中的 `dq_rule_version_id`。

发布谓词：

```text
不存在 OPEN BLOCKER
AND 不存在未获授权 WAIVE 的 OPEN ERROR
AND 行数/Bin/身份/单位/规格等强制对账通过
```

BLOCKER 的 `waivable` 必须为 0。

## 27. evaluation.rule_set / rule_version / evaluation_run

Rule Version 覆盖 SPEC/BIN/PAT/SBL/CPK/SPC/OTHER，保存算法代码版本、参数 JSON、业务 Owner、批准状态与生效时间。

Evaluation Run 保存 Dataset Version、Rule Version、Scope Key、Filter JSON/Hash、样本数、排除数、执行状态和开始/结束时间。v0.6 新写入的 `measurement_evaluation.evaluation_run_id` 业务必填；汇总型结果进入 `evaluation.metric_result`。

为兼容 v0.4 历史记录，`0002` 首次加列时数据库物理字段暂为 `NULL`。升级后先完成历史评价回填与对账，再由后续 revision 改为 `NOT NULL`；应用服务从 v0.6 上线起不得产生新的空关联记录。

## 28. delivery.export_job / export_artifact

Export Job 保存请求人、Dataset Version、Filter、Evaluation Context、Template Version、状态与错误。Artifact 保存对象 URI、文件名、MIME、大小、SHA-256、过期时间。下载行为另写 Audit Log。

## 29. Retest 顺序

Final disposition 不能只使用 `MAX(unit_result.attempt_no)`。统一排序键为：

```text
(test_run.run_attempt_no, unit_result.attempt_no, source sequence)
```

若厂商提供显式 Final/Retest Disposition，则由版本化规则优先决定。计算结果保存 Rule Version 和选择证据。
