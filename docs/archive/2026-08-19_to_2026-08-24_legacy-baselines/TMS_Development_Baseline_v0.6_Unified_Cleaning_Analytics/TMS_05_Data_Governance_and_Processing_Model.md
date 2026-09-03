# TMS v0.6 数据治理与 Processing Model

## 1. 目的

本文件冻结那些如果不提前定义、上线后最容易导致“同一个数据不同人算出不同答案”的规则。

---

## 2. 事实 / 规则 / 结果边界

### Immutable Facts

不可因业务规则修改而覆盖：

```text
Source File bytes/hash
Test Run 原始识别信息
Unit / Die identity evidence
X/Y
Raw Soft/Hard Bin
Raw Tester Result
Measurement raw/numeric/text/status
```

### Versioned Rules

```text
Parser Version
Spec Set / Binding
Bin Mapping Set
PAT/SBL Baseline/Algorithm Version
Scope Priority
```

### Derived Results

```text
Measurement Evaluation
Bin Evaluation
Yield
CPK
PAT/SBL Result
SPC
```

派生结果可重算，必须能追溯到规则版本。

---

## 3. Spec Resolver 冻结算法

输入 Context：

```text
supplier
product
test_stage
program_version
customer
quality_grade
package
event_time_utc
evaluation_scope_key
```

算法：

1. 只取 `active=1`、Spec Set `RELEASED`、有效期覆盖 event time 的 Binding。
2. Binding 中非空字段必须与 Context 相等。
3. Join `scope_priority`。
4. 找到最高 `priority`。
5. 最高 priority 候选：
   - 1 条：选中。
   - 0 条：`NO_MATCH`。
   - >1 条：`CONFIG_AMBIGUOUS`。
6. 选中 Spec Set 后再按 `test_item_id` 优先、`canonical_parameter_code` fallback 匹配 Spec Item。
7. 将 spec_binding_id、spec_item_id、应用 LSL/USL/operator **快照**写入 measurement_evaluation。

### Program Limit Fallback

Tester Program 的 Limit 是“测试程序事实/配置”，不自动等于公司受控 Spec。

只有 evaluation context 配置 `allow_program_limit_fallback=true` 时，在 Controlled Spec `NO_MATCH` 后才可 fallback，并在 evaluation_reason 明确：

```text
PROGRAM_LIMIT_FALLBACK
```

---

## 4. Bin Resolver 冻结算法

输入：

```text
supplier/product/test_stage/program_version/event_time/bin_type/raw_bin_code
```

步骤与 Spec 相同：scope match → priority → 最高候选必须唯一 → bin code match。

结果写入 `unit_bin_evaluation`。

**raw bin code 永不被映射后的 failure mode 替换。**

---

## 5. Retest 冻结规则

### 5.1 全部 Attempt 保留

禁止 Parser/Loader：

```text
只保留最后一次
只保留 PASS
用第二次覆盖第一次
```

### 5.2 FPY

每个 `logical_unit_key` 的最小 `attempt_no`。

### 5.3 Final Yield

默认按 `(run_attempt_no, attempt_no, source_sequence)` 的最大确定性顺序选择；如果厂商文件提供显式 final/retest disposition，以版本化规则决定优先级。禁止只使用 `MAX(attempt_no)` 跨 Run 选最终记录。

### 5.4 Run Retest 与 Unit Retest 分开

```text
run_attempt_no  = 整个 Wafer/Lot Session
attempt_no      = 单颗 Unit/Die
```

---

## 6. 重复上传冻结规则

### SHA256 相同

- `source_file` 不新增。
- 新增 `source_file_receipt`。
- 标记 `is_duplicate_receipt=1`。
- 默认不启动新 processing run。

### Force Reprocess

必须填写 reason，并记录 audit。

Force Reprocess：

```text
同 source_file
→ 新 processing_job
→ 新 processing_run
```

而不是复制 source file/业务数据。

---

## 7. Parser 升级重跑

Parser Version 必须不可变：

```text
huahong_cp 1.0.0
huahong_cp 1.1.0
```

禁止：

```text
同一个 1.0.0 代码内容被静默替换
```

建议记录 `code_checksum` / Git Commit。

### 发布事务

新 run 通过 DQ 后：

```text
BEGIN TRANSACTION
old current → SUPERSEDED / is_current=0
new run     → PUBLISHED / is_current=1
COMMIT
```

失败则旧 current 不受影响。

---

## 8. Data Quality Gate

建议三档：

### BLOCKER

禁止发布：

```text
源文件损坏
无法识别 Product/Lot
CP 同一 attempt 出现重复 X/Y 且无法解释
Test Item 定义冲突
Spec Resolver CONFIG_AMBIGUOUS（对必须评价的上下文）
```

### ERROR

默认禁止发布；只有 `dq_rule_version.waivable=1` 且用户具备权限时才能 Waive：

```text
大面积非法数字
关键 Bin 缺失
时间不可解析
```

### WARNING

允许发布但展示：

```text
FT 无稳定 Serial
Source timezone 使用 supplier default
部分参数 NOT_TESTED 比例异常
```

人工 Waive 必须写 audit log。

BLOCKER 的 `waivable` 固定为 0，任何角色都不能直接豁免；必须修复输入、格式档案、规则或清洗器并产生新的 Processing Run。

---

## 9. 时区冻结规则

### 系统内部

全部 UTC：

```text
SYSUTCDATETIME()
```

字段命名：

```text
created_at_utc
started_at_utc
evaluated_at_utc
```

### 源文件

保留：

```text
source_started_local
source_timezone_iana
source_utc_offset_minutes
timezone_resolution
```

### 禁止

- 不允许根据 SQL Server OS 时区隐式转换。
- 不允许把无时区的源字符串直接写进 `started_at_utc`。
- 不允许前端把服务器时间当作业务时间。

---

## 10. Audit 冻结范围

需要审计：

- Spec/Binding 发布、修改、废止。
- Bin Mapping 发布、修改、废止。
- Scope Priority 变化。
- Parser 默认版本切换。
- Force Reprocess。
- DQ Issue Waive/Resolve。
- 用户手工修正 Master Data Mapping。

无需逐行审计：

- 每一条 measurement insert。
- 可重算 analytics summary 的后台刷新。

---

## 11. 数据删除策略

第一阶段：

- Source File：原则上不物理删除。
- Published/Superseded Processing Run：不物理删除。
- Canonical facts：不因 Parser 重跑删除旧 run 数据。
- Derived evaluation/summary：可按 retention 重算/清理，但必须保留生成版本和必要历史。

后续数据量巨大后再设计 archive/partition retention，不在 v0.4 提前做物理删除优化。

## 12. v0.6 Input Set 与多文件任务

用户每次提交先创建 `import_batch`，每个文件 Receipt 通过 `import_batch_file` 固定角色和顺序。Format Profile 定义必需/可选角色及同一任务是否允许多文件。

```text
Input Set
├─ DETAIL #1
├─ DETAIL #2
├─ SPEC
└─ MANIFEST（可选）
```

同一 Input Set 中若检测出多个不兼容 Format Profile、产品身份冲突或单位合同冲突，必须 BLOCKER；不得拆开后分别成功而让用户误以为同一 Dataset 已完整发布。

## 13. Format Profile 与 Cleaner Release

Format Profile 版本描述文件签名、目录/Sheet/Header、字段顺序、参数身份、单位、无效值、Pass/Bin、文件角色和兼容范围。Cleaner Release 记录代码版本与 checksum，并声明支持的 Profile Version。

生产执行只能选择 `RELEASED` Profile + `RELEASED` Cleaner 组合。Auto Detect 只生成候选和证据；未知或多候选时进入 `NEEDS_CONFIRMATION`，不能猜测。

## 14. Dataset 发布事务

```text
BEGIN TRANSACTION
  检查 Processing Run READY
  检查 DQ 发布谓词
  创建 Dataset Version
  固化 Dataset Version ↔ Processing Run
  旧 current Dataset Version → is_current=0 / SUPERSEDED
  新 Dataset Version → PUBLISHED / is_current=1
  写 Audit Log
COMMIT
```

Dataset Version 是用户分析、保存项目、评价和导出的唯一数据锚点；Source File 或 Processing Run 不能单独冒充正式数据集。

## 15. Evaluation Run

Spec/Bin/PAT/SBL/CPK/SPC 计算先创建 Evaluation Run，固定 Dataset Version、Rule Version、Scope、Filter Hash、算法参数和排除规则。前端修改 Sigma 倍数等参数时必须创建新的临时或正式 Evaluation Run，并明确是否获准用于业务交付。

图表层不得为了“看起来正常”静默剔除 IQR 异常点。若规则确实要求排除，Evaluation Run 必须返回排除条件、排除数量和可钻取记录。

## 16. Saved Analysis 与 Export

Saved Analysis 保存 Dataset Version、规范化筛选和图表配置。Dataset 后续有新版本时，历史项目仍打开原版本并提示可升级。

Export Job 对当前页和完整结果采用同一授权/审计主线，保存模板版本和 Artifact SHA。浏览器本地生成文件只能作为开发预览，不能作为正式业务导出。

## 17. 用户与权限

首版采用 RBAC + Data Scope。每次读取 Source File、Dataset Version、Measurement、Saved Analysis、Export Artifact 和治理对象都执行对象级授权。管理员可以管理账号与系统，但不自动获得业务数据下载权限。

## 18. 兼容输出

CP 的 cleaned/yield/spec、FT 的厂家 Excel、PAT 与 SYL/SBL 是版本化 Delivery Template，不是 Canonical Fact。兼容输出必须能追溯到 Dataset Version、Evaluation Run（如适用）和模板版本；不同厂家的字段保留合同不能被统一导出模板擅自覆盖。
