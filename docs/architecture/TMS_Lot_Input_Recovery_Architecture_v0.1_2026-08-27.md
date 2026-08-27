# TMS Lot 人工补录恢复架构 v0.1（2026-08-27）

## 1. 状态与适用范围

状态：**已实现并完成受控真实验收；仍有生产上线门禁**。

本架构解决正式 FT Route A 中的异常容错场景：已批准格式能够确认厂家、产品和数据结构，但 Cleaner 无法从源文件名取得业务 Lot。系统不得伪造 Lot，也不得把缺 Lot 当成普通成功；它暂停当前任务，要求有权限的用户按源文件补录，随后使用同一 Cleaner Release 重跑并重新执行格式、身份、Spec 和 Canonical 校验。

当前真实端到端验收分别覆盖日月新 FT 和日月光 FT，两者使用同一平台状态机和各自独立 Adapter。CP 当前正式支持的华虹、Jetech、立昂微样本能够完整取得 Lot，因此本报告不把 FT 验收外推为“CP 缺 Lot 已完成真实样本验收”。

## 2. 核心架构决定

1. **缺 Lot 是可恢复暂停，不是成功，也不是伪造值的理由。** Cleaner 只能在确认缺失字段为 `LOT_ID` 时发出结构化输入请求；厂家、格式、产品、参数结构或 Spec 冲突仍按失败关闭。
2. **人工值不直接写入 Canonical。** 用户补录形成独立 `field_enrichment` 审计事实，恢复 Job 把它作为按源文件限定的 override 交回原 Cleaner Release；只有 Cleaner 和 FT Writer 再次校验通过后才发布 Dataset。
3. **原 Job 不复活。** 被阻塞 Job 保持 `NEEDS_INPUT`，恢复操作创建新的 `INITIAL_IMPORT` 子 Job，并通过 `parent_job_id` 保留完整尝试链。
4. **一次解决当前阻塞 Job 的全部请求。** 多文件任务必须提交全部 OPEN 请求；系统不允许只解决其中一部分后恢复，避免部分源文件仍缺 Lot。
5. **人工 Lot 不能覆盖已解析事实。** 数据库服务拒绝对已有当前 Lot enrichment 或已有解析 Lot 的范围再次补录；当前 FT Adapter 还会比较文件名解析值和人工 override，冲突时失败关闭。
6. **Spec 继续跟随 Source Run/Lot。** 补录只提供缺失身份，不提供 Spec；重跑后的 scatter spec、source identity 和规格指纹仍由原 Cleaner 输出，FT Writer 按 Source Run、Lot 和规格指纹建立或复用 Program Version/Spec Set。
7. **原文件不可变。** 平台以登记 SHA256 校验输入，把精确登记的 FT 文件复制到隔离临时目录后再运行 Cleaner；人工补录不修改原文件，也不改变其内容身份。

## 3. 状态机

```text
Batch RECEIVED
  -> Batch QUEUED / Job 1 QUEUED
  -> Batch PROCESSING / Job 1 RUNNING
  -> Cleaner emits LOT_ID input marker
  -> Batch NEEDS_INPUT / Job 1 NEEDS_INPUT / Request OPEN
  -> user resolves all OPEN requests in one transaction
       - field_enrichment FILL is created
       - Request becomes RESOLVED
       - Job 2 is created with parent_job_id=Job 1
       - Batch returns to QUEUED
  -> Batch PROCESSING / Job 2 RUNNING
  -> same Cleaner Release reruns with per-file Lot override
  -> artifacts and Canonical facts are validated
  -> Dataset Version PUBLISHED + Current
  -> result summary visible / Batch PROCESSED / Job 2 SUCCESS
```

允许的恢复链只有 `RUNNING -> NEEDS_INPUT` 和 `NEEDS_INPUT -> 新建子 Job`。通用 Job 状态修改接口不能迁移 `INITIAL_IMPORT`；批次处于 `NEEDS_INPUT` 时，普通“重新处理”和通用 enrichment 入口都必须拒绝，防止绕过专用恢复事务。

## 4. Cleaner 子进程协议

FT 发布 Adapter 在确认唯一缺失身份是 Lot 时输出一行：

```text
TMS_INPUT_REQUIRED_JSON={"field_code":"LOT_ID","files":[{"original_file_name":"example.xlsx"}],"message":"..."}
```

Marker 合同要求：

- 只有一个 marker，且必须是合法 JSON；
- 顶层字段只能是 `field_code`、`files`、`message`；
- `field_code` 必须为 `LOT_ID`；
- 文件项只能包含纯文件名 `original_file_name`，不得包含目录或重复名称；
- 发布 FT Adapter 当前使用退出码 `42`；成功退出时出现 marker 属于协议错误；
- 未输出合法 marker 的非零退出仍是 Cleaner 失败，不能转为人工补录。

Worker 只把 marker 中的文件名匹配到当前 Import Batch 的 receipt。FT 输入在调用前按不区分大小写的文件名检查唯一性，并复制到临时隔离目录；复制前后均与登记 SHA256 对账。override 只能命中本次已登记的原始文件名。

## 5. API 冻结合同

### 5.1 查询待补录文件

```http
GET /api/v1/{engineering|production}/{cp|ft}/uploads/{batch_id}/input-requests
Permission: DATASET_READ
```

响应字段：

```json
{
  "import_batch_id": 51,
  "status": "NEEDS_INPUT",
  "field_code": "LOT_ID",
  "prompt": "...",
  "latest_job_id": 66,
  "requests": [
    {
      "input_request_id": 7,
      "source_file_id": 12345,
      "original_file_name": "example.xlsx",
      "current_value": null
    }
  ]
}
```

`source_file_id` 上例仅表示字段形状，不是批次 51 的实际编号。调用者必须使用服务返回的 `input_request_id`，不能自行构造请求范围。

### 5.2 保存并恢复

```http
POST /api/v1/{engineering|production}/{cp|ft}/uploads/{batch_id}/input-requests/resolve
Permission: TASK_CREATE
Content-Type: application/json
```

```json
{
  "resolutions": [
    {"input_request_id": 7, "lot_id": "FA53-4115"}
  ],
  "reason": "根据生产或测试记录人工确认 Lot"
}
```

成功响应：

```json
{
  "import_batch_id": 51,
  "job_id": 67,
  "status": "QUEUED"
}
```

Lot 在服务端执行 Unicode NFKC、去首尾空格和大写归一化；只允许字母、数字及 `-._/+` 分隔符。当前日月新/日月光正式 Adapter 进一步要求已批准的 `4 位字母或数字-4 位数字` 格式。API 拒绝额外字段、重复 request ID、空原因和部分请求提交。

## 6. 数据模型与事务边界

| 对象 | 作用 | 关键约束 |
|---|---|---|
| `ingestion.processing_job` | 保存每次不可变处理尝试 | `NEEDS_INPUT` 状态；恢复 Job 用 `parent_job_id` 指向阻塞 Job |
| `ingestion.import_batch` | 前端展示当前业务状态 | `NEEDS_INPUT` 是显式批次状态；恢复时以 CAS 返回 `QUEUED` |
| `ingestion.processing_input_request` | 保存按 receipt 的缺字段请求 | 当前只允许 `LOT_ID`；同一 Batch/receipt/field 同时最多一个 OPEN 请求 |
| `ingestion.field_enrichment` | 保存人工补录事实 | `action='FILL'`、人工值、操作者、原因、源文件范围和 current 标志 |
| `governance.audit_log` | 保存状态变化审计 | 记录阻塞 Job、恢复 Job、请求 ID 和操作者 |

进入 `NEEDS_INPUT` 时，Job、Batch 和全部 input request 在同一个数据库事务中更新。解决请求时，服务使用 `UPDLOCK/HOLDLOCK` 锁定 Batch 与 request，完成以下原子操作：

1. 校验 Batch 的 Domain、Stage、Owner 和 `NEEDS_INPUT` 状态；
2. 校验提交 ID 精确等于当前全部 OPEN 请求，且来自同一个阻塞 Job；
3. 创建按源文件的 `LOT_ID/FILL` enrichment；
4. 把请求更新为 `RESOLVED` 并关联 enrichment、操作者和时间；
5. 创建复用原 `cleaner_release_id` 的 `INITIAL_IMPORT` 子 Job；
6. 把 Batch 以条件更新改为 `QUEUED`；
7. 写入审计日志。

相同 request ID 和相同 Lot 的客户端重试返回已存在的恢复 Job；同一请求改用不同 Lot 会返回冲突。上传和普通重跑通过锁定 Batch、状态 CAS 与 Job 创建同一事务完成，避免并发创建两个活动的正式导入 Job。

## 7. 权限与 Owner 隔离

- 查询需要 `DATASET_READ`，解决需要 `TASK_CREATE`；
- 普通用户只能访问 `import_batch.owner_user_id` 等于自己的批次；
- `SYSTEM_ADMIN` 可以跨 Owner 处理；
- 无权访问时统一返回不存在语义，避免泄露其他用户的 Batch、文件名、Job 或 Lot；
- 恢复 Job 的 `requested_by` 和 `requested_by_user_id` 由当前 Principal 写入，客户端不能冒用；
- 输入文件下载仍按 Batch Owner/管理员范围校验。

## 8. Lot、Spec 与 Canonical 一致性

人工补录不是对 `test.test_run.lot_id` 的直接 UPDATE。恢复 Job 调用原 FT Cleaner，Cleaner 生成带 Lot 的 cleaned/scatter data/scatter spec/manifest；FT Writer 再执行以下校验：

- Product、Factory、Source_ID、Lot 和行数在各 artifact 之间一致；
- 每个 Source Run 都存在对应 spec；
- 规格按 Source Run/Lot 计算指纹；不同指纹使用独立 Program Version/Spec Set；
- 同一 Dataset 存在多套规格时，不伪造单一 Dataset `spec_set_id`；
- 分析查询先按 Lot 限定 Source，再读取该 Source Run 的参数、单位、限值和测试条件。

只有上述校验通过后，数据才进入 `test.test_run -> test.unit_result -> test.measurement` 正式 Canonical 链和 Dataset Current。人工值保留在 enrichment 中，原始解析事实、人工确认事实和最终 Canonical 结果可分别追溯。

## 9. SQL Server 2014 实现约束

数据库迁移为 `sql2014_0014`。实现使用 SQL Server 2014 支持的 `datetime2`、`SYSUTCDATETIME()`、filtered index、`TOP`、`UPDLOCK/HOLDLOCK` 和普通关系表；JSON 只作为 `nvarchar(max)` 审计证据由应用层编码，不使用 `OPENJSON` 或 `JSON_VALUE`。

## 10. 真实验收锚点

2026-08-27 受控浏览器验收形成以下可复查链：

| 项目 | 结果 |
|---|---|
| Import Batch | 51 |
| 阻塞 Job | 66，`NEEDS_INPUT` |
| Input Request | 7，最终 `RESOLVED` |
| Enrichment | 16，`LOT_ID/FILL`，值 `FA53-4115` |
| 恢复 Job | 67，`parent_job_id=66`，最终 `SUCCESS` |
| Dataset | 22 / Version 1，`PUBLISHED` 且 Current |
| Test Run | 186 |
| Canonical 数量 | 4,962 Unit / 89,316 Measurement |
| 原文件 SHA256 | `C0894974020EB652815051FADCF01D3757DFC60FC25542B157E85A6D95D74529`，补录前后不变 |

负向验收 Batch 50 故意误选厂家，系统按厂家/格式合同严格拒绝，没有把格式错误转换为 Lot 补录，也没有发布 Dataset。

同日第二条真实日月光链形成：Batch 52；Job 68=`NEEDS_INPUT`；Request 8=`RESOLVED`；Enrichment 17=`LOT_ID/FILL FA54-9744`；Job 69 的 `parent_job_id=68` 且最终 `SUCCESS`；Dataset 23 / Version 1 为 Current；Test Run 187 包含 3,900 Unit 和 93,600 Measurement。真实源文件与去 Lot 文件名副本 SHA256 均为 `C36A3E064FF980818A78868295B1410387E5EF5F6C3724B81CBBA4AE23157D92`，重跑后不变。

## 11. 残余生产门禁

1. **任意绝对 `source_path`**：当前 TASK_CREATE 用户仍可登记后端可读取的任意允许后缀路径。生产前必须改为受管 Source Catalog/允许根目录，并做解析后路径包含与链接检查。
2. **跨事务最终发布**：Canonical Writer 发布 Dataset Current、结果摘要、Batch 状态和 Job 最终成功尚未处于一个原子 finalize 边界。生产前应采用 staged publish、幂等 finalize 或可靠补偿/对账机制。
3. **多文件 `processing_run` 只关联首来源**：FT Dataset 可以包含多个 Source Run，但当前 `processing_run.source_file_id` 只保存批次第一份源文件。需要建立 processing-run/source 映射，补齐逐源 lineage 和数据库级 parsed-Lot 防覆盖证据。
4. **补录值无原批次纠正流程**：已经解决的 Lot 请求不能在原批次内 supersede。补错时当前安全恢复方式是重新上传，不能在原 Dataset 上原地改 Lot；后续如建设纠正能力，必须形成新审计事实和新 Dataset Version。
